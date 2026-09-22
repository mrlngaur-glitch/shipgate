"""Shared machinery for the three hook entrypoints — stdin JSON parsing, the
project-local ledger (`.shipgate/ledger.db`, never the dogfood corpus), and the
timestamp convention already established in `shipgate.ledger`'s own tests.

Confirmed hook stdin fields (verified against Claude Code's own hooks reference before
writing this module, not assumed from memory):
`session_id`, `cwd`, `hook_event_name`, `transcript_path`, `permission_mode` are common
to every hook event; `PreToolUse`/`PostToolUse` add `tool_name`, `tool_input`,
`tool_use_id`; `PostToolUse` additionally adds `tool_result`; `Stop` adds
`stop_hook_active`. No hook event includes a `model` field or a timestamp — both are
transcript-file facts, not hook-input facts, so `events.model` is left `None` for every
row this package writes; enriching it is JSONL-ingest territory (task 1.5), not this
package's job.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shipgate.ledger.writer import LedgerWriter

#: Not one of `sessions`/`subagent` per se — a live hook has no way to tell, from the
#: hook JSON alone, whether it fired inside the top-level session or a sub-agent's own
#: context (Claude Code's hook-to-subagent interaction isn't confirmed as of this
#: writing). Defaulting to "session" is a stated simplification, not a silent guess —
#: see the module docstring in `shipgate/hooks/__init__.py`.
DEFAULT_TRANSCRIPT_TIER = "session"


class HookInputError(ValueError):
    """The hook's stdin wasn't usable JSON, or was missing a field every entrypoint
    needs (`session_id`, `cwd`). Callers treat this as "skip, don't crash" — see each
    entrypoint's `run()`."""


class ProjectRootUnresolvableError(HookInputError):
    """**Founder finding, this session: hooks trusted the payload's `cwd` absolutely.**
    Reproduced live: a `cwd` that does not exist (or exists but isn't a directory) made
    `open_project_ledger`'s old unconditional `mkdir(parents=True, exist_ok=True)`
    silently create an entire new directory tree — anywhere on disk — and write a real
    45,056-byte ledger there, while the real project got no `.shipgate/` at all. Both
    `PreToolUse` and `Stop` exited 0 with empty stdout AND empty stderr — the gate was
    completely absent and every observable signal said fine. Same trust-boundary class
    as the shipfile-integrity gap (also known, also accepted for now, also not yet
    fixed): the gate trusts an external, unverified piece of data absolutely. That one
    is the shipfile; this one is the hook payload.

    Raised the moment `Path(cwd)` is found not to already be a real directory, before a
    single filesystem write is attempted. A hook must never create a project root that
    doesn't already exist — an unresolvable `cwd` is a refusal to proceed, not an
    invitation to invent one. Every caller still fails OPEN on the decision (the
    loop-breaker rule stands, exit 0, the stop/tool-call itself is never blocked by
    this), but never SILENTLY: see each entrypoint's `run()` for the stderr line this
    produces. `shipgate/hooks/stop.py`'s P12 `gate_unavailable.json` escalation
    mechanism does NOT apply to this error — that marker lives at
    `<cwd>/.shipgate/gate_unavailable.json`, and if `cwd` itself doesn't resolve there is
    structurally nowhere legitimate to write it without repeating the exact bug this
    class exists to stop. Named, accepted residual gap, the same shape as P12's own
    "if `.shipgate/` itself is locked down" gap: this failure is loud on stderr every
    single time, with no durable escalation counter, because there is no safe place to
    keep one.

    **Named residual, confirmed by the founder's own independent edge-case sweep, not
    fixed on purpose:** `Path(cwd).is_dir()` only catches `cwd` not resolving at all. A
    `cwd` that resolves to a REAL directory that just isn't the intended project (e.g. a
    misconfigured but otherwise valid path) is indistinguishable from a legitimate
    project root by anything this function can check — `.shipgate/` gets created there,
    same as it would for the actual intended project. This is not the bug this class
    exists to stop (nothing is created that didn't already exist; no directory tree is
    invented), and there is no way to verify "is this the RIGHT directory" without a
    canonical answer to compare against, which nothing in a hook payload provides.
    `shipgate.discipline.session.NoSessionRecordedError`'s corrected message already
    covers exactly this case — "confirm this is the same directory your Claude Code
    session is actually running in" — so the residual is named and surfaced, not
    silently unhandled."""


def read_hook_input(stdin_text: str | None) -> dict[str, Any]:
    """Parse the hook's stdin JSON. `stdin_text=None` reads real stdin (production);
    tests pass a literal string so no subprocess/stdin plumbing is needed to exercise
    the parsing and ledger-writing logic.

    **Founder finding: the production path used to read stdin as
    TEXT (`sys.stdin.read()`), whose decoder — and, critically, its ERROR HANDLER — is
    environment-controlled, not this module's choice. `pretooluse.py`'s own docstring
    promised "never raises... not an uncaught traceback," and a same-environment repro
    of that promise held (this environment's `sys.stdin.errors` happens to be
    `surrogateescape`, which degrades undecodable bytes instead of raising) — but the
    founder's own cross-environment matrix proved the promise false wherever the host's
    stdin decoder is `strict` (confirmed live: `PYTHONIOENCODING=cp932`, plain `ascii`):
    a real hook payload containing nothing more exotic than a non-ASCII byte (a Japanese
    path segment, an em dash) raised an uncaught `UnicodeDecodeError` before
    `except HookInputError` could ever see it — the gate did not fire, silently, and an
    environment variable was enough to disable it. **One environment proving a claim
    doesn't fail is not the same as the claim holding — see the standing rule this
    finding produced, P24: a "never raises" claim must name the environment it was
    tested in, or be tested across the axis that could falsify it.** Fixed here, at the
    one shared reader every hook entrypoint calls, not per-hook: bytes are read
    explicitly (`sys.stdin.buffer.read()`) and decoded as UTF-8 under an explicit,
    stated error policy (`errors="strict"`) — the decoder is now this function's choice,
    never the host machine's. A decode failure is treated exactly like a JSON parse
    failure (a hook payload that isn't valid UTF-8 isn't valid JSON either — both are
    "not usable input," both take the existing honest-skip `HookInputError` path, no new
    branch invented for it).
    """
    if stdin_text is not None:
        text = stdin_text
    else:
        raw = sys.stdin.buffer.read()
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise HookInputError(f"hook input is not valid UTF-8: {exc}") from exc
    if not text.strip():
        raise HookInputError("empty hook input on stdin")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HookInputError(f"hook input is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HookInputError(f"hook input must be a JSON object, got {type(data).__name__}")
    for required in ("session_id", "cwd"):
        if not data.get(required):
            raise HookInputError(f"hook input is missing required field {required!r}")
    return data


def ensure_utf8_streams() -> None:
    """**Investigated as a hypothesis alongside `shipgate.cli`'s confirmed Gate C
    blocker (Session 011), then precisely scoped rather than assumed to be a second
    instance of the identical bug.** Every hook entrypoint's stderr messages use U+2014
    (em dash), several written **outside any try/except** (they *are* the handler for a
    routine condition — missing hook input, an invalid shipfile) — the same shape that
    crashed `shipgate.cli`'s stdout writes. Verified directly rather than assumed
    symmetric: `sys.stdout`'s default error handler on a narrow codepage is `'strict'`
    (crashes) — confirmed by the CLI's own real crash — but **`sys.stderr`'s default
    error handler is `'backslashreplace'`, a CPython built-in, independent of platform
    or locale.** A negative-control test (temporarily disabling this function) proved
    empirically that none of this package's existing stderr writes actually crash on
    cp437/cp932 — `sys.stderr` already degrades a non-encodable character to `\\uXXXX`
    text instead of raising. `stop.py`'s one stdout write is `json.dumps(...)`, which
    ASCII-escapes non-ASCII by construction (`ensure_ascii=True` default) and was never
    at risk either. **This function is kept as defense-in-depth, not as the fix for a
    proven live crash in this package:** it makes both streams explicitly UTF-8 rather
    than relying on an easy-to-forget CPython default, so a *future* stderr write added
    without this context in mind (or any stdout write ever added to a hook) is already
    covered. Wrapped in its own `try`/`except`, consistent with this package's zero-
    crash discipline, since even a defensive fix must never be what breaks a hook."""
    try:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001, S110 — silent by design: logging here could itself
        pass  # write to the very stream this function exists to make safe


def utc_now_iso() -> str:
    """`YYYY-MM-DDTHH:MM:SSZ` — the exact format already used throughout
    `tests/unit/test_ledger.py`'s fixtures, kept consistent rather than inventing a
    second timestamp convention."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_project_ledger(cwd: str) -> LedgerWriter:
    """Opens (creating if needed) `<cwd>/.shipgate/ledger.db`. `corpus_root=Path(cwd)`
    so `sessions.source_dir` can honestly be `"."` — a live hook-originated session
    isn't an ingest of some external corpus; it *is* the corpus root, trivially, of
    itself. This function never references `shipgate.ledger.paths.DEFAULT_CORPUS_ROOT`
    or `~/.claude/projects` in any way — the dogfood corpus is not merely convention-
    protected here, it is unreachable from this code path by construction.

    **Root-cause fix, this session: refuses to create `cwd` itself.** `.shipgate/` is
    created under `cwd` (expected — that's this project's own state directory, not the
    project root), but `cwd` must already exist as a real directory *before* that
    happens. The old code's `mkdir(parents=True, exist_ok=True)` made no such
    distinction — given an unresolvable `cwd` it happily built out the whole missing
    tree from wherever `cwd` started, anywhere on disk, and wrote a real ledger there.
    See `ProjectRootUnresolvableError`'s docstring for the full reproduction and
    reasoning."""
    root = Path(cwd)
    if not root.is_dir():
        raise ProjectRootUnresolvableError(
            f"hook payload cwd {cwd!r} does not exist (or is not a directory) — refusing to "
            "create a project root ShipGate did not find"
        )
    db_path = root / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return LedgerWriter(db_path, corpus_root=root)


def ensure_session(writer: LedgerWriter, *, session_id: str, cwd: str) -> None:
    """Idempotent: writes a `sessions` row the first time this `session_id` is seen,
    does nothing on every hook fired afterward in the same session. Reads via the raw
    connection (`LedgerWriter.connection` is documented as available to callers that
    aren't bypassing the append-only triggers — a `SELECT` isn't a bypass)."""
    exists = writer.connection.execute(
        "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if exists is None:
        writer.insert_session(
            session_id=session_id,
            project_slug=Path(cwd).name or "project",
            source_dir=".",
            cwd=cwd,
        )


#: Written immediately BEFORE gate evaluation begins, and paired with a terminal row
#: (`gate_evaluation`, or `GATE_INCOMPLETE_RECORD_TYPE` below) once it ends.
GATE_STARTED_RECORD_TYPE = "gate_started"

#: Written when a `GATE_STARTED_RECORD_TYPE` row is found with no terminal row after it
#: — i.e. a gate evaluation that began and never finished.
GATE_INCOMPLETE_RECORD_TYPE = "gate_incomplete"

#: Record types that close an open `gate_started`.
_GATE_TERMINAL_RECORD_TYPES = ("gate_evaluation", GATE_INCOMPLETE_RECORD_TYPE)

#: How far back `reconcile_abandoned_gates` looks. A bound, not a guess about
#: importance: this runs inside a hook, so it must never become a full-table scan on a
#: long-lived ledger. Anything older than this has already been reconciled by one of the
#: many hook invocations since — the only way to accumulate more than this many
#: unreconciled rows is for the gate to die 50 times in a row without a single
#: successful evaluation in between, which is itself reported the first time it happens.
_GATE_RECONCILE_LOOKBACK = 50


def reconcile_abandoned_gates(writer: LedgerWriter, *, source_file: str, now: str) -> list[str]:
    """Turns "the gate never finished" from silence into a ledger row. Returns the
    `session_id` of every abandoned evaluation it recorded (empty list: nothing to do).

    **Pilot finding, 2026-09-11 (pilot project): "a gate that fails open silently is worse
    than no gate at all, because the project's doctrine now tells everyone they are
    protected."** Before this, a `Stop` hook that was killed mid-evaluation — host
    timeout, crash, machine sleep, `TerminateProcess` — wrote nothing at all. So
    "the gate passed", "the gate never ran" and "the gate was killed" were *identical*
    in the ledger, and the only one of the three that looked any different was the one
    that never happens. That is exactly the unmeasured-and-zero-are-the-same-value
    failure this product exists to stop agents committing.

    **Why a sentinel row and not a signal handler.** A `SIGTERM`/`SIGINT` handler that
    writes the row on the way down is the obvious design and it is the wrong one here:
    it cannot work for `SIGKILL`, and on Windows — the platform this is actually
    deployed on — a killed hook is normally `TerminateProcess`, which delivers no signal
    at all and runs no handler. A handler would therefore be a mechanism that looks like
    a guarantee and silently isn't, on the one platform that matters most. Writing the
    sentinel BEFORE the work starts and reconciling it at the next hook survives every
    kill mode on every platform, because it never needs the dying process to do anything.

    **Why this is not a background daemon** (this project's own standing rule forbids
    them): it does no polling and starts no process. It runs only when a hook is already
    firing for its own reasons — deferred work at a natural touchpoint, which is the
    shape that rule prescribes."""
    started_rows = writer.connection.execute(
        "SELECT event_id, session_id FROM events WHERE record_type = ? "
        "ORDER BY event_id DESC LIMIT ?",
        (GATE_STARTED_RECORD_TYPE, _GATE_RECONCILE_LOOKBACK),
    ).fetchall()
    if not started_rows:
        return []

    oldest_considered = min(row[0] for row in started_rows)
    terminal_ids = [
        row[0]
        for row in writer.connection.execute(
            "SELECT event_id FROM events WHERE event_id > ? AND record_type IN "
            f"({','.join('?' * len(_GATE_TERMINAL_RECORD_TYPES))}) ORDER BY event_id",
            (oldest_considered, *_GATE_TERMINAL_RECORD_TYPES),
        ).fetchall()
    ]

    # A `gate_started` is abandoned when no terminal row falls between it and the NEXT
    # `gate_started`. Bounding by the next start (rather than just "any later terminal
    # row") is what makes repeated deaths each get their own row instead of one later
    # success retroactively excusing all of them.
    starts_ascending = sorted(started_rows)
    abandoned: list[tuple[int, str]] = []
    for index, (start_id, session_id) in enumerate(starts_ascending):
        next_start_id = starts_ascending[index + 1][0] if index + 1 < len(starts_ascending) else None
        closed = any(
            terminal_id > start_id and (next_start_id is None or terminal_id < next_start_id)
            for terminal_id in terminal_ids
        )
        if not closed:
            abandoned.append((start_id, session_id))

    for start_id, session_id in abandoned:
        writer.insert_event(
            session_id=session_id,
            source_file=source_file,
            source_offset=0,
            transcript_tier=DEFAULT_TRANSCRIPT_TIER,
            record_type=GATE_INCOMPLETE_RECORD_TYPE,
            timestamp=now,
            raw_payload={
                "abandoned_gate_started_event_id": start_id,
                "detected_at": now,
                "reason": (
                    "a gate evaluation began and never recorded an outcome. The hook "
                    "process did not finish -- killed by the host's hook timeout, "
                    "crashed, or the machine stopped. This is NOT a pass: no condition "
                    "was confirmed, and the session it belonged to was allowed to end "
                    "ungated."
                ),
            },
        )
    return [session_id for _, session_id in abandoned]


__all__ = [
    "DEFAULT_TRANSCRIPT_TIER",
    "GATE_INCOMPLETE_RECORD_TYPE",
    "GATE_STARTED_RECORD_TYPE",
    "HookInputError",
    "ProjectRootUnresolvableError",
    "ensure_session",
    "ensure_utf8_streams",
    "open_project_ledger",
    "read_hook_input",
    "reconcile_abandoned_gates",
    "utc_now_iso",
]
