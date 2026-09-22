"""The retry-cap loop-breaker (task 2.6, Phase 2) — the piece that makes
`Stop` actually gate. Ties `shipgate.shipfile` (the contract), `shipgate.gate.checkers`
(real evidence), and `shipgate.ledger` (the append-only record) together into one
decision per `Stop`-hook invocation: block, or release — and if release, say honestly
whether that's because the gate is green or because the retry cap was hit first.

Report §8.1 Week 2 done-condition: "Cap exhausted → honest red report, Stop released, no
loop." This project's own standing rule: "an infinite enforcement loop is a worse failure than a lie."

**Two design decisions the report doesn't specify at implementation level — both argued
and the plan reviewed before this module was written, restated here
since they're load-bearing for how this module works:**

1. **Retries never touch the frozen verdict-transition table.**
   `shipgate.verdicts.transitions` has no self-loop for `UNVERIFIED` or `CONTRADICTED` —
   only `VERIFIED`, `UNVERIFIED_VACUOUS`, and `PENDING_RECHECK` may repeat themselves
   (see that module's docstring). A real retry can easily produce the *same* `CONTRADICTED`
   result twice in a row (the agent's fix didn't work; the failure persists) — calling
   `LedgerWriter.insert_verdict` on every attempt would raise `IllegalTransitionError` on
   exactly the ordinary case this module exists to handle. Rather than add those
   self-loops to a transition table that predates and forms part of Gate A's sign-off
   (reopening a frozen interface without asking), this module writes a new `verdicts` row
   only when the class actually changes, or repeats one of the three classes the table
   already allows to self-loop — see `_should_write_new_verdict_row`. A repeated,
   unchanged result is still fully recorded: every attempt writes one `events` row
   (`record_type="gate_evaluation"`) with the complete per-condition detail, whether or
   not it produced a new `verdicts` row. `verdicts` stays "belief state, taxonomy-
   constrained"; `events` stays "complete raw observation trail."

2. **The retry cap fires before Claude Code's own silent 8-consecutive-block override,
   always.** Founder review finding: the first version of this
   reasoning cited "8" as merely "verified in two research passes" — the same grade of
   unsourced claim that got the hook-timeout figure wrong in an earlier session (that one
   was later confirmed correct, but the *belief* backing it wasn't evidence; this session
   holds the "8" to the same standard, not a lower one just because the first citation
   error happened to land on a different number). Re-verified against the official
   documentation, same standard as `reporters.pytest_reporter`'s 600s citation — exact
   quote, exact URL: https://code.claude.com/docs/en/hooks-guide, section "Stop hook hits
   the block cap" — **"Claude Code overrides a Stop hook after it blocks eight times in a
   row without progress."** The same page documents an escape hatch: **"If your hook
   legitimately needs more than eight iterations to converge, raise the cap with
   `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`."** — meaning 8 is Claude Code's own *default*, not
   an absolute constant; a deployment can raise it, never (per the docs) lower it, so this
   module's fixed margin below 8 stays conservative (more room, never less) for any
   deployment that hasn't overridden the environment variable, and is stated here as a
   named limitation for one that has.

   `gate_policy.max_retries` is schema-bounded to a maximum of 10 (frozen at Gate A) — a
   shipfile author setting 9 or 10 would never actually see this module's own honest-red
   release under Claude Code's *default* cap; Claude Code would silently take over first,
   with no ledger marker at all. Since the schema's bound can't be changed, this module
   applies a defensive *internal* ceiling below it — `effective_max_retries =
   min(shipfile_max_retries, _MAX_EFFECTIVE_RETRIES)` — so the honest-red release always
   happens with real margin before Claude Code's override could ever pre-empt it, for any
   legal `max_retries` value. No shipfile-visible *behavior* change for the common case
   (`max_retries <= 6`, including the default of 3) — but per the founder's second
   finding below, a shipfile that sets `max_retries` above the ceiling is never left to
   discover the truncation silently: see "design decision 3."

   The docs also document the intended idiom for a hook that might block: check
   `stop_hook_active` and exit early (allow) the moment it's `true`, rather than
   evaluating again. This module deliberately does **not** follow that simplest idiom —
   see `shipgate/hooks/stop.py`'s module docstring for why the ledger's own exact
   attempt count is used instead of that boolean flag, and the stated limitation of not
   doing so.

3. **A ceiling that silently narrows a user's declared config is exactly the behavior
   this product blocks agents for — founder review finding.** The original version of
   this module applied `effective_max_retries` without ever telling anyone it had done
   so: the frozen schema advertises `max_retries` up to 10, a shipfile author setting 8
   would silently get 6, and neither the ledger nor any hook output ever named the
   truncation. Fixed: `GateEvaluation.ceiling_binding` (`True` whenever
   `configured_max_retries > effective_max_retries`) is computed every attempt, written
   into the `gate_evaluation` event's payload every time regardless of outcome, and
   folded into `block_reason` / the new `release_reason` (see below) whenever it's
   actually binding — visible in the one place a `Stop`-hook attempt has to say anything
   today, not buried in a comment. **Standing rule going forward (this is the third
   instance of this exact failure shape — `check_tests_pass`'s old caveat-in-prose,
   the pre-Gate-A version-error ordering, now this): if the code knows something the
   user would want to know, the verdict or the visible output carries it, never a
   comment alone.**

4. **Flake quarantine (task 2.7) fingerprints in `events`, never a new `verdicts`
   column, never encoded into `verdicts.reason`.** Founder-decided design: the same
   belief/observation split as design decision 1 — a content fingerprint is raw
   observation, not belief, so it belongs in the `gate_evaluation` event payload every
   attempt already writes, not a new ledger column (its own high-risk item, not
   pre-cleared) and not smuggled into a human-readable `reason` string (rejected
   explicitly: that would make `reason` machine-parseable and freeze its wording by
   accident). `compute_project_fingerprint` hashes every file `iter_scanned_files`
   would walk — **project-wide, not per-condition**, a deliberate smaller-build choice:
   it can under-detect flakiness (an unrelated file change masks a real flake) but can
   never over-detect one (mislabel a genuine regression as "flaky" when the code
   actually changed) — the safer direction to be imprecise in, named here rather than
   silently accepted.

   A condition is reclassified `QUARANTINED_FLAKY` when its claim's current verdict is
   `VERIFIED` or `CONTRADICTED`, this attempt's raw checker result is the *other* one of
   those two classes, and this attempt's fingerprint equals the *immediately preceding*
   attempt's fingerprint (code provably unchanged between the two looks). A claim
   currently `QUARANTINED_FLAKY` always writes `UNVERIFIED` next, regardless of what
   this attempt's raw check observed — the only legal edge out of quarantine
   (`shipgate.verdicts.transitions`: "quarantine lifted, back to a clean baseline
   pending fresh evidence, never straight back to a hard verdict") — the raw result is
   still recorded in full in this attempt's `events` payload, just not promoted to
   `verdicts` directly; the *next* attempt, now starting from `UNVERIFIED`, is free to
   assert a real verdict again. Both cases are `advisory_only` on `ConditionOutcome`:
   report §5.4 — "degraded to advisory, never hard-block" — excluded entirely from the
   green/red decision, neither blocking nor counting as passing.

5. **Session blast-radius (task 2.8) lives in its own module,
   `shipgate.gate.blast_radius`, and is deliberately not called from here.** See that
   module's docstring for the full reasoning: it classifies a "high-risk change" from
   the shipfile's own declared `task_classes[name].risk_tier`, not an invented
   detection heuristic, and the report frames blast-radius as a "while the agent works"
   concern distinct from this module's "is the work done" concern.

6. **`inventory_complete` gains a narrow, explicit bridge (task 2.6→2.7 follow-up,
   founder-approved new scope): a claims sidecar file.** `check_inventory_complete`
   needs an external `claimed_items` list this orchestrator has no pipeline to produce
   automatically (no claims-extraction from a transcript exists — task 1.5 territory).
   If `<project_root>/.shipgate/claims/<condition_id>.json` exists, it's read as exactly
   the `claimed_items` shape that function already takes and dispatched through it for
   real — no new data shape invented. Absent file: unchanged, honest non-dispatchable
   gap, exactly as before this bridge existed. Present but malformed: dispatchable, but
   `UNVERIFIED`/`observed=False` naming the parse failure — informative, not a silent
   skip and not a crash. This bridge only removes the "nothing can ever supply the
   list" blocker; it does not build the extractor that would supply it automatically.

7. **A checker that raises must never reach `shipgate/hooks/stop.py`'s outer fail-open
   catch — founder review finding, "Finding 5," a launch blocker.**
   That catch exists for genuinely unanticipated internal errors (report §3.6: an
   infinite block loop is worse than a lie, so an unknown fault must not hang the
   session) — but a checker's own subprocess-decode failure is an *anticipatable*
   class, not an unknown one, and treating it identically to "the gate never ran" was
   itself the exact vacuous-pass hole this project's whole discipline exists to close:
   an unhandled exception mid-evaluation silently released `Stop` with no per-condition
   detail, no ledger `verdicts` row for the affected claim, and no distinction from "the
   shipfile has nothing checkable." Both checker-dispatch call sites below
   (`run_checker` and `check_inventory_complete`) are now wrapped in a narrow
   `try/except Exception`: any exception a checker raises is converted, right here,
   into an honest `CheckResult(Verdict.UNVERIFIED_VACUOUS, observed=False, reason=...)`
   naming the exception — no new verdict class (the frozen taxonomy already has exactly
   the right meaning for "ran, observed nothing"), no fabricated diagnosis of *why* it
   couldn't observe anything, just the real exception type and message. This flows
   through the rest of `evaluate_gate` exactly like any other vacuous result: a real
   `verdicts` row, named in `block_reason`, `should_block=True` on a fresh claim. `stop.
   py`'s own outer catch stays as the boundary for whatever's left — errors ShipGate's
   own code genuinely could not have anticipated — this decision narrows what falls into
   that category, it doesn't remove the category.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`. This module
depends on `shipgate.shipfile`, `shipgate.ledger`, `shipgate.verdicts`, and its sibling
`shipgate.gate.checkers` only.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shipgate.ledger.writer import LedgerWriter
from shipgate.shipfile import Shipfile
from shipgate.verdicts import EvidenceTier, Verdict

from .checkers import (
    CHECKERS,
    DEFAULT_CHECKER_TIMEOUT_SECONDS,
    CheckResult,
    check_inventory_complete,
    iter_scanned_files,
    run_checker,
)

#: Where `shipgate/hooks/stop.py` looks for a project's shipfile, relative to `cwd`.
#: Not part of the frozen shipfile *format* (that's `shipgate.shipfile`'s concern) — this
#: is a gate-orchestration convention, so it lives here, not there.
DEFAULT_SHIPFILE_FILENAME = "shipfile.yaml"

#: Claude Code's own *default* consecutive-block override — verified with a direct quote
#: and URL, same standard as `reporters.pytest_reporter`'s 600s figure. See "design
#: decision 2" above for the exact citation and the `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`
#: escape hatch that can raise (never, per the docs, lower) it.
_CLAUDE_CODE_CONSECUTIVE_BLOCK_LIMIT = 8

#: The defensive ceiling this module applies to `gate_policy.max_retries` (schema max:
#: 10) — chosen to leave real margin (2 full attempts) below
#: `_CLAUDE_CODE_CONSECUTIVE_BLOCK_LIMIT`, not to shave it as close as possible. See
#: "design decision 2" above; regression-guarded by
#: `test_effective_retry_ceiling_stays_below_the_claude_code_override_with_margin`.
_MAX_EFFECTIVE_RETRIES = 6

#: Verdict classes the frozen transition table allows to legally follow themselves
#: (`shipgate.verdicts.transitions`) — see "design decision 1" above.
_SELF_LOOP_LEGAL_VERDICTS = frozenset({Verdict.VERIFIED, Verdict.UNVERIFIED_VACUOUS, Verdict.PENDING_RECHECK})

#: Where an `inventory_complete` condition's `claimed_items` can be supplied today —
#: see "design decision 6" above. One file per condition, named by the condition's own
#: `id` so multiple `inventory_complete` conditions in one shipfile don't collide.
SIDECAR_CLAIMS_DIRNAME = ".shipgate/claims"


@dataclass(frozen=True)
class ConditionOutcome:
    """One `done_conditions` entry's outcome for one gate-evaluation attempt."""

    condition_id: str
    dispatchable: bool
    check_result: CheckResult | None  # None when not dispatchable — see module docstring
    verdict_row_written: bool  # False when this attempt's result was a no-op self-loop
    advisory_only: bool  # True for a flake-quarantine or quarantine-lift outcome — see design decision 4


@dataclass(frozen=True)
class GateEvaluation:
    """One `Stop`-hook attempt's complete answer: what happened, and what the hook should
    do about it (`should_block` / `exhausted`)."""

    prior_blocking_attempts: int  # prior attempts where should_block was True -- see evaluate_gate
    effective_max_retries: int
    ceiling_binding: bool  # True iff the shipfile's own max_retries was actually truncated
    conditions: list[ConditionOutcome]
    all_dispatchable_green: bool
    should_block: bool
    exhausted: bool
    block_reason: str | None
    release_reason: str | None  # populated when exhausted=True — the honest-red summary


def _should_write_new_verdict_row(current: Verdict | None, new: Verdict) -> bool:
    """`False` only for a same-class repeat of a class the frozen transition table does
    NOT allow to self-loop (`UNVERIFIED`, `CONTRADICTED`, and — though no checker
    produces it today — `TARGET_UNREACHABLE`/`QUARANTINED_FLAKY`). `True` for a claim's
    first-ever verdict, any class change, and the three classes the table already allows
    to repeat (`VERIFIED`, `UNVERIFIED_VACUOUS`, `PENDING_RECHECK`) — see the module
    docstring's design decision 1."""
    if current is None:
        return True
    if new != current:
        return True
    return new in _SELF_LOOP_LEGAL_VERDICTS


def _ensure_claim(writer: LedgerWriter, *, claim_id: str, session_id: str, condition: dict[str, Any], now: str) -> None:
    """Idempotent: writes a `claims` row the first time this `claim_id` is seen, does
    nothing on every retry afterward — same pattern as `shipgate.hooks._common.
    ensure_session`."""
    exists = writer.connection.execute("SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
    if exists is None:
        text = condition.get("description") or f"{condition.get('type')}: {condition['id']}"
        writer.insert_claim(
            claim_id=claim_id,
            session_id=session_id,
            text=text,
            source="shipfile_condition",
            shipfile_condition_ref=condition["id"],
            created_at=now,
        )


#: Length, in hex characters, `compute_project_fingerprint` returns. **Self-caught bug,
#: not a design preference**: a full 64-char `sha256().hexdigest()` is a long,
#: contiguous, digit-and-letter alphanumeric run — exactly the generic shape
#: `shipgate.ledger.redaction._GENERIC_SECRET_RUN` (threshold: 24+ chars) exists to
#: catch, on the reasonable theory that a random-looking 64-char token is far more
#: likely to be a leaked credential than a legitimate payload value. `insert_event`
#: cannot be asked to skip redaction for this one field — every payload is redacted
#: unconditionally, by design, and that's correct; carving out an exception here would
#: be the wrong fix for the wrong reason. The full digest got silently replaced with
#: the literal string `"[REDACTED]"` on every write, which a same-session combined-
#: realistic test caught: comparing a freshly computed digest against `[REDACTED]`
#: read back from the ledger is never equal, so flake detection silently never fired.
#: Fixed at the root: truncate to a length that's genuinely, honestly not secret-shaped
#: rather than disguising or evading the redaction pattern. 16 hex chars (64 bits) is
#: comfortably under the 24-char threshold and has a ~2^-64 accidental-collision
#: probability — irrelevant precision loss for this use case (comparing two specific
#: digests for equality within one session), nowhere near a cryptographic boundary.
_FINGERPRINT_HEX_LENGTH = 16


#: Files at or above this size are **excluded from the fingerprint entirely** — not
#: read, and not even contributing their `(size, mtime)`.
#:
#: **Pilot finding, 2026-09-11 (pilot project), root-caused here rather than where it was
#: filed.** The pilot reported the `Stop` gate as never completing and blamed the
#: shipfile's `done_conditions` command. Measured, that was wrong — the command finished
#: inside `DEFAULT_CHECKER_TIMEOUT_SECONDS`; the pilot's own ledger records it as
#: `command exited 0`. The four minutes were spent *here*: the previous version of this
#: function read every byte of every file `iter_scanned_files` walks, which on that
#: project was **47.2 GB across 17,692 files, measured at 297.03s** — including two
#: multi-gigabyte database snapshots and two 4.7 GB model weight files. Nothing bounded
#: it, and it ran before any condition was evaluated, on every single `Stop`.
#:
#: Two separate defects, fixed together because they share a cause (treating "every file
#: under the root" as "the code"):
#:
#: 1. **Cost.** Content hashing is replaced by `(relpath, size, mtime_ns)` — see this
#:    function's docstring for what that trades away and why it is acceptable *for this
#:    specific signal*.
#: 2. **Signal quality.** A multi-gigabyte artifact that is rewritten every session
#:    (a DB snapshot, a rotated log, a model checkpoint) changes the digest every time,
#:    so `fingerprint_unchanged` would be permanently `False` and flake detection could
#:    never fire at all. Excluding oversize files makes the signal track *source*, which
#:    is the only thing it was ever meant to answer a question about.
#:
#: 8 MiB: comfortably above any plausible hand-written source file, comfortably below
#: the artifact sizes above. A source file bigger than this is vanishingly rare, and
#: missing one degrades flake detection (advisory-only, never a hard-block) rather than
#: affecting any verdict.
_FINGERPRINT_MAX_FILE_BYTES = 8 * 1024 * 1024

#: Wall-clock ceiling for the fingerprint walk itself. The stat-only walk above is
#: ~1,000x cheaper than the byte-reading one it replaces, but "cheaper" is not "bounded"
#: — a network mount, a stalled filesystem, or a pathological tree can still hang a walk
#: indefinitely, and this runs inside a hook the host is timing. On exhaustion the walk
#: stops and reports itself **incomplete** rather than returning a digest that silently
#: describes only part of the tree: see `ProjectFingerprint.complete`.
_FINGERPRINT_WALK_BUDGET_SECONDS = 5.0

#: How recently a file may have been modified before this fingerprint refuses to be
#: used as a "nothing changed" signal.
#:
#: **This constant exists because the metadata fingerprint broke a documented invariant
#: and the breakage was caught by it happening, not by reasoning about it.** Design
#: decision 4 (module docstring) states the fingerprint "can under-detect flakiness ...
#: but can never over-detect one (mislabel a genuine regression as 'flaky' when the code
#: actually changed)." Content hashing gave that for free. Metadata hashing does not: two
#: writes of the same byte length landing inside one filesystem timestamp tick produce an
#: identical `(size, mtime_ns)` pair, so a real edit becomes invisible and a genuine
#: regression could be suppressed as `QUARANTINED_FLAKY` — exactly the direction that
#: invariant forbids.
#:
#: This was not hypothetical. The test written for this fix passed in isolation and
#: FAILED in the full suite run, where load pushed two same-length writes into the same
#: tick. That is the defect reproducing itself, in this repository, within minutes of
#: being introduced.
#:
#: Restoring the invariant by content-hashing again is not available: the files *under*
#: the size cap on the pilot's tree still total 1.41 GB, measured — so "only hash the
#: small ones" is still a gigabyte of reads per `Stop`. Instead the fingerprint declines
#: to answer: if anything it hashed was modified within this window, the tree has not
#: settled, `mtime_settled` is False, and the digest is not comparable to any other.
#: Flake detection switches off for that attempt and the checker's real verdict stands
#: unmodified — under-detection, which the invariant permits.
#:
#: 2 seconds covers the worst timestamp granularity in practical use (FAT/exFAT's 2s)
#: and every finer one (NTFS 100ns, ext4 1ns), so the window is bounded by the filesystem
#: rather than by a guess about how fast an agent edits files.
_FINGERPRINT_MTIME_TRUST_WINDOW_NS = 2_000_000_000


@dataclass(frozen=True)
class ProjectFingerprint:
    """The digest plus what was actually observed to produce it. The counts are not
    decoration: they are written into the `gate_evaluation` event so that a digest can
    always be audited for what it covered, rather than being an opaque 16 hex characters
    whose meaning silently changed when this function did.

    Two independent reasons a digest may not be usable as a "nothing changed" signal,
    kept as separate fields because they have separate causes and separate fixes:

    - `complete` is `False` when the walk hit `_FINGERPRINT_WALK_BUDGET_SECONDS` before
      finishing, so the digest describes only part of the tree.
    - `mtime_settled` is `False` when something it hashed was modified within
      `_FINGERPRINT_MTIME_TRUST_WINDOW_NS`, so metadata cannot be trusted to have
      recorded a change that may have happened.

    `comparable` is the only thing callers should test. Both failure modes resolve the
    same way — refuse to compare — because the only thing a fingerprint match is allowed
    to do is *suppress* a real verdict by calling it flaky."""

    digest: str
    files_hashed: int
    files_skipped_oversize: int
    complete: bool
    mtime_settled: bool
    duration_seconds: float

    @property
    def comparable(self) -> bool:
        """Whether this digest may be compared against another to conclude "unchanged."""
        return self.complete and self.mtime_settled


def compute_project_fingerprint_detail(project_root: Path) -> ProjectFingerprint:
    """A deterministic `sha256` over every file `iter_scanned_files` would walk under
    `project_root` and that is under `_FINGERPRINT_MAX_FILE_BYTES`, hashed as sorted
    `(relpath, size, mtime_ns)` triples and truncated to `_FINGERPRINT_HEX_LENGTH` hex
    characters (see that constant's docstring for why). Reused as flake detection's
    "did the code change between two looks" signal; two calls against an unchanged tree
    always produce the same digest.

    **What changed, and what it costs — stated plainly rather than left for someone to
    discover.** This used to hash file *contents*. It now hashes file *metadata*. That
    is strictly a weaker signal: an edit that leaves both size and modification time
    byte-identical is invisible to it. In practice that requires either a deliberate
    timestamp restore or a filesystem with worse than nanosecond mtime resolution for
    two writes inside the same tick.

    This is acceptable **for this signal specifically, and would not be for a verdict.**
    The only thing a fingerprint match does is let `_classify_with_flake_detection`
    downgrade a flipped verdict to `QUARANTINED_FLAKY`, which is advisory-only and can
    never hard-block (this project's own standing rule). So a missed change degrades to
    "no flake detected" — the check's real result stands, unmodified. It can never turn
    a red into a green. A fingerprint is not evidence and is never recorded as evidence;
    `EvidenceTier` is what carries that, and nothing here touches it."""
    started = time.monotonic()
    digest = hashlib.sha256()
    files_hashed = 0
    files_skipped_oversize = 0
    complete = True
    # 0 means "nothing hashed yet". An empty tree therefore reports settled, which is
    # correct: there is nothing whose change could have been missed.
    newest_mtime_ns = 0

    # The deadline must cover the WALK, not just the hashing loop. Self-caught while
    # measuring this fix: with the check only inside the loop below, the pilot's tree
    # spent its entire budget enumerating files and then reported
    # `files_hashed=0, complete=False` -- a technically-honest answer that was useless,
    # because the expensive part had already happened before the first check ran.
    collected: list[Path] = []
    for candidate in iter_scanned_files(project_root, ["."]):
        if time.monotonic() - started > _FINGERPRINT_WALK_BUDGET_SECONDS:
            complete = False
            break
        collected.append(candidate)

    files = sorted(collected, key=lambda p: p.relative_to(project_root).as_posix())
    for file_path in files:
        if time.monotonic() - started > _FINGERPRINT_WALK_BUDGET_SECONDS:
            complete = False
            break
        try:
            stat = file_path.stat()
        except OSError:
            continue  # vanished mid-scan -- skipped, not a crash (as before)
        if stat.st_size >= _FINGERPRINT_MAX_FILE_BYTES:
            files_skipped_oversize += 1
            continue
        digest.update(file_path.relative_to(project_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
        digest.update(b"\0")
        files_hashed += 1
        newest_mtime_ns = max(newest_mtime_ns, stat.st_mtime_ns)

    # A negative difference (mtime in the future -- clock skew, a network mount) is
    # caught by the same comparison: it is not >= the window, so the tree is treated as
    # unsettled. Refusing to compare is the safe answer for an unexplained clock too.
    mtime_settled = (time.time_ns() - newest_mtime_ns) >= _FINGERPRINT_MTIME_TRUST_WINDOW_NS

    return ProjectFingerprint(
        digest=digest.hexdigest()[:_FINGERPRINT_HEX_LENGTH],
        files_hashed=files_hashed,
        files_skipped_oversize=files_skipped_oversize,
        complete=complete,
        mtime_settled=mtime_settled,
        duration_seconds=time.monotonic() - started,
    )


def compute_project_fingerprint(project_root: Path) -> str:
    """The digest alone, for callers that don't need the provenance counts. Kept so the
    name every existing caller and test already uses keeps working unchanged."""
    return compute_project_fingerprint_detail(project_root).digest


def _prior_fingerprint(writer: LedgerWriter, session_id: str) -> str | None:
    """The `project_fingerprint` recorded on this session's immediately preceding
    `gate_evaluation` event, or `None` if there isn't one (the first attempt — which
    can never flake-detect, consistent with `QUARANTINED_FLAKY` never being a claim's
    first verdict either)."""
    row = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE session_id = ? AND record_type = 'gate_evaluation' "
        "ORDER BY event_id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return json.loads(row[0]).get("project_fingerprint")


def _classify_with_flake_detection(
    current_class: Verdict | None, raw_result: CheckResult, fingerprint_unchanged: bool
) -> tuple[Verdict, EvidenceTier | None, str, bool]:
    """Returns `(effective_verdict, effective_evidence_tier, effective_reason,
    advisory_only)` — see the module docstring's design decision 4. `raw_result` is
    never mutated; its own verdict/tier/reason are always what's recorded in this
    attempt's raw `events` payload regardless of what this function returns for
    `verdicts`."""
    if current_class is Verdict.QUARANTINED_FLAKY:
        reason = (
            "quarantine lifted pending fresh evidence -- the only legal transition out of "
            "QUARANTINED_FLAKY is to UNVERIFIED (shipgate.verdicts.transitions); this attempt's "
            f"raw check observed {raw_result.verdict.value} ({raw_result.reason}), recorded in "
            "this attempt's event but not promoted to a hard verdict yet -- the next evaluation may."
        )
        return Verdict.UNVERIFIED, None, reason, True

    if (
        current_class in (Verdict.VERIFIED, Verdict.CONTRADICTED)
        and raw_result.verdict in (Verdict.VERIFIED, Verdict.CONTRADICTED)
        and raw_result.verdict is not current_class
        and fingerprint_unchanged
    ):
        reason = (
            f"flake detected: verdict flipped {current_class.value} -> {raw_result.verdict.value} "
            "while the project's content fingerprint was unchanged since the immediately preceding "
            f"attempt ({raw_result.reason}) -- quarantined, advisory only, never hard-block"
        )
        return Verdict.QUARANTINED_FLAKY, None, reason, True

    return raw_result.verdict, raw_result.evidence_tier, raw_result.reason, False


def _safe_dispatch(condition: dict[str, Any], fn: Callable[..., CheckResult], *args: Any) -> CheckResult:
    """Calls a checker function (`fn(*args)`, expected to return a `CheckResult`) and
    converts ANY exception it raises into an honest, blocking `CheckResult` instead of
    letting it propagate out of `evaluate_gate` — see the module docstring's design
    decision 7. The reason string names the real exception type and message; never a
    fabricated diagnosis of what specifically went wrong beyond that."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 -- deliberate fail-closed boundary, design decision 7
        return CheckResult(
            condition["id"],
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=(
                f"checker raised {type(exc).__name__}: {exc} while evaluating this "
                "condition -- could not observe anything, not a pass"
            ),
            observed=False,
        )


def _load_sidecar_claimed_items(project_root: Path, condition_id: str) -> tuple[list[dict] | None, str | None]:
    """Returns `(claimed_items, error)`. `(None, None)` means no sidecar file exists at
    all — the honest, unchanged "still not dispatchable" case. `(None, <message>)` means
    a sidecar file exists but couldn't be used. A non-`None` first element is a real,
    usable `claimed_items` list — see module docstring's design decision 6."""
    path = project_root / SIDECAR_CLAIMS_DIRNAME / f"{condition_id}.json"
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(item, dict) and "match" in item for item in data):
            raise ValueError("expected a JSON array of {'match': ..., 'disposition': ...} objects")
    except (OSError, ValueError) as exc:
        return None, str(exc)
    return data, None


def evaluate_gate(
    shipfile: Shipfile,
    project_root: Path,
    session_id: str,
    writer: LedgerWriter,
    *,
    now: str,
    timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS,
) -> GateEvaluation:
    """Runs every dispatchable `done_conditions` entry for real, records the outcome,
    and decides whether this `Stop` should block. Safe to call repeatedly for the same
    `session_id` — each call is one attempt; the retry count is derived from the ledger
    itself, not passed in, so a fresh hook process each turn still sees the true count.

    **The retry cap counts prior BLOCKING attempts, not every prior evaluation.** An
    attempt where every condition was green, or where the only non-green conditions
    were `advisory_only` (flake-quarantined), never asked the agent to do anything — it
    would be wrong to spend that session's retry budget on an attempt that never
    actually retried anything. Self-caught while testing task 2.7: without this
    distinction, two flake-quarantine attempts (which never block) would silently eat
    two of the session's real retries, exhausting the cap early on the very first
    attempt that had a genuine, actionable failure."""
    all_prior_events = writer.connection.execute(
        "SELECT raw_payload_redacted FROM events WHERE session_id = ? AND record_type = 'gate_evaluation'",
        (session_id,),
    ).fetchall()
    prior_attempts_total = len(all_prior_events)
    prior_blocking_attempts = sum(
        1 for (payload,) in all_prior_events if payload and json.loads(payload).get("should_block")
    )

    configured_max_retries = shipfile["gate_policy"]["max_retries"]
    effective_max_retries = min(configured_max_retries, _MAX_EFFECTIVE_RETRIES)
    ceiling_binding = configured_max_retries > effective_max_retries
    ceiling_note = (
        f"NOTE: shipfile gate_policy.max_retries={configured_max_retries} was capped at "
        f"{effective_max_retries} to stay below Claude Code's own default 8-consecutive-block "
        "Stop-hook override (see shipgate/gate/orchestrator.py) — the requested retry budget "
        "was not fully honored."
        if ceiling_binding
        else None
    )

    fingerprint = compute_project_fingerprint_detail(project_root)
    this_fingerprint = fingerprint.digest
    prior_fp = _prior_fingerprint(writer, session_id)
    # An INCOMPLETE walk is never comparable. A truncated walk can produce a digest that
    # happens to equal a prior one (both stopped early at the same point) while the tree
    # underneath actually changed -- and a fingerprint match's only power is to suppress
    # a real verdict by relabelling it `QUARANTINED_FLAKY`. Requiring completeness keeps
    # the failure direction conservative: an unbounded tree loses flake detection, never
    # gains a wrongly-suppressed red.
    fingerprint_unchanged = fingerprint.comparable and prior_fp is not None and prior_fp == this_fingerprint

    outcomes: list[ConditionOutcome] = []
    event_conditions: list[dict[str, Any]] = []

    for condition in shipfile["done_conditions"]:
        cond_type = condition.get("type")

        if cond_type == "inventory_complete":
            claimed_items, sidecar_error = _load_sidecar_claimed_items(project_root, condition["id"])
            if claimed_items is None and sidecar_error is None:
                # No sidecar file at all -- unchanged, honest non-dispatchable gap,
                # exactly as before this bridge existed. See design decision 6.
                outcomes.append(
                    ConditionOutcome(condition["id"], dispatchable=False, check_result=None, verdict_row_written=False, advisory_only=False)
                )
                event_conditions.append({"id": condition["id"], "type": cond_type, "dispatchable": False})
                continue
            if sidecar_error is not None:
                raw_result = CheckResult(
                    condition["id"], Verdict.UNVERIFIED, None,
                    reason=f"claims sidecar file at {SIDECAR_CLAIMS_DIRNAME}/{condition['id']}.json is malformed: {sidecar_error}",
                    observed=False,
                )
            else:
                raw_result = _safe_dispatch(
                    condition, check_inventory_complete, condition, project_root, claimed_items, timeout
                )
        elif cond_type not in CHECKERS:
            # ears-only entries (no "type" at all) and emission_traced (no checker
            # built, no assigned task) — excluded from the green/red decision, never
            # silently treated as passing. See module docstring's dispatch note.
            outcomes.append(
                ConditionOutcome(condition["id"], dispatchable=False, check_result=None, verdict_row_written=False, advisory_only=False)
            )
            event_conditions.append({"id": condition["id"], "type": cond_type, "dispatchable": False})
            continue
        else:
            raw_result = _safe_dispatch(condition, run_checker, condition, project_root, timeout)

        claim_id = f"{session_id}:{condition['id']}"
        _ensure_claim(writer, claim_id=claim_id, session_id=session_id, condition=condition, now=now)

        current = writer.current_verdict(claim_id)
        current_class = current[1] if current is not None else None
        effective_verdict, effective_tier, effective_reason, advisory_only = _classify_with_flake_detection(
            current_class, raw_result, fingerprint_unchanged
        )
        write_row = _should_write_new_verdict_row(current_class, effective_verdict)
        if write_row:
            writer.insert_verdict(
                claim_id=claim_id,
                verdict=effective_verdict,
                evidence_tier=effective_tier,
                reason=effective_reason,
                created_at=now,
            )

        effective_result = CheckResult(condition["id"], effective_verdict, effective_tier, effective_reason, raw_result.observed)
        outcomes.append(
            ConditionOutcome(
                condition["id"], dispatchable=True, check_result=effective_result,
                verdict_row_written=write_row, advisory_only=advisory_only,
            )
        )
        event_conditions.append(
            {
                "id": condition["id"],
                "type": cond_type,
                "dispatchable": True,
                "advisory_only": advisory_only,
                "raw_verdict": raw_result.verdict.value,
                "raw_reason": raw_result.reason,
                "verdict": effective_verdict.value,
                "reason": effective_reason,
                "observed": raw_result.observed,
            }
        )

    # Advisory outcomes (a flake quarantine, or the lift-out-of-quarantine step) never
    # drive the block/green decision either way — report §5.4, "degraded to advisory,
    # never hard-block". See module docstring's design decision 4.
    dispatchable_outcomes = [o for o in outcomes if o.dispatchable]
    blocking_outcomes = [o for o in dispatchable_outcomes if not o.advisory_only]
    # Zero blocking conditions (every dispatchable condition either doesn't exist, or is
    # currently advisory-only) is itself a vacuous/non-decidable gate, not a green one —
    # `bool(blocking_outcomes)` guards against "nothing was actually checked" silently
    # rendering as a pass, the same discipline every individual checker already applies.
    all_green = bool(blocking_outcomes) and all(
        o.check_result is not None and o.check_result.verdict is Verdict.VERIFIED for o in blocking_outcomes
    )

    should_block = False
    exhausted = False
    block_reason: str | None = None
    release_reason: str | None = None
    failing = [
        f"{o.condition_id}: {o.check_result.verdict.value} ({o.check_result.reason})"
        for o in blocking_outcomes
        if o.check_result is not None and o.check_result.verdict is not Verdict.VERIFIED
    ]
    # Three distinct not-green shapes, deliberately handled differently:
    # 1. A real, non-advisory failure exists (`failing` non-empty) -> block/exhaust as
    #    normal, exactly as before task 2.7.
    # 2. Nothing was ever dispatchable at all (no `blocking_outcomes` AND no
    #    `dispatchable_outcomes`) -> still block/exhaust: a shipfile with nothing
    #    checkable is a real configuration problem, not something to release quietly.
    # 3. `dispatchable_outcomes` is non-empty but EVERY one of them is `advisory_only`
    #    (every condition currently flake-quarantined) -> never block, full stop, no
    #    exception — report §5.4, "degraded to advisory, never hard-block" means never,
    #    even when advisory is literally the only thing on the table this attempt.
    #    Released with `should_block=False, exhausted=False`: not a confirmed green
    #    (still reported honestly via `all_dispatchable_green=False`), not a retry-cap
    #    exhaustion either (nothing to have a cap on this attempt) — the ledger's
    #    per-condition `advisory_only` flags are the honest record of why.
    if not all_green and (failing or not dispatchable_outcomes):
        if not failing:
            failing = ["(no dispatchable done_conditions at all -- nothing was checked, not a pass)"]
        if prior_blocking_attempts < effective_max_retries:
            should_block = True
            block_reason = "ShipGate: gate not green — " + "; ".join(failing)
        else:
            exhausted = True
            release_reason = (
                "ShipGate: retry cap exhausted, releasing Stop without blocking further "
                "(honest red, not a pass) — still failing: " + "; ".join(failing)
            )
        # The ceiling note is a fact about THIS attempt's own retry budget, so it belongs
        # on whichever reason this attempt actually produced — never silently dropped
        # just because this particular attempt happened to block rather than exhaust,
        # or vice versa. See "design decision 3."
        if ceiling_note and should_block:
            block_reason = f"{block_reason} | {ceiling_note}"
        elif ceiling_note and exhausted:
            release_reason = f"{release_reason} | {ceiling_note}"

    writer.insert_event(
        session_id=session_id,
        source_file="<gate-orchestrator>",
        source_offset=0,
        transcript_tier="session",
        record_type="gate_evaluation",
        timestamp=now,
        raw_payload={
            "attempt": prior_attempts_total + 1,
            "prior_blocking_attempts": prior_blocking_attempts,
            "effective_max_retries": effective_max_retries,
            "ceiling_binding": ceiling_binding,
            "project_fingerprint": this_fingerprint,
            # What the digest above actually covered. Recorded because a bare 16-char
            # digest is unauditable: without these, a reader cannot tell a fingerprint
            # that walked the whole tree from one that stopped at the budget, nor know
            # that oversize artifacts were deliberately excluded rather than missed.
            "project_fingerprint_files_hashed": fingerprint.files_hashed,
            "project_fingerprint_files_skipped_oversize": fingerprint.files_skipped_oversize,
            "project_fingerprint_complete": fingerprint.complete,
            "project_fingerprint_mtime_settled": fingerprint.mtime_settled,
            "project_fingerprint_comparable": fingerprint.comparable,
            "project_fingerprint_duration_seconds": round(fingerprint.duration_seconds, 3),
            "all_dispatchable_green": all_green,
            "should_block": should_block,
            "exhausted": exhausted,
            "release_reason": release_reason,
            "conditions": event_conditions,
        },
    )

    return GateEvaluation(
        prior_blocking_attempts=prior_blocking_attempts,
        effective_max_retries=effective_max_retries,
        ceiling_binding=ceiling_binding,
        conditions=outcomes,
        all_dispatchable_green=all_green,
        should_block=should_block,
        exhausted=exhausted,
        block_reason=block_reason,
        release_reason=release_reason,
    )


__all__ = [
    "DEFAULT_SHIPFILE_FILENAME",
    "SIDECAR_CLAIMS_DIRNAME",
    "ConditionOutcome",
    "GateEvaluation",
    "ProjectFingerprint",
    "compute_project_fingerprint",
    "compute_project_fingerprint_detail",
    "evaluate_gate",
]
