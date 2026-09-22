"""Hook wiring diagnostics — verifies the *installation*, not the shipfile.

**Why this exists, found live (Session 042, fleet-rollout D-3 defect report from
a pilot project's own analyst):** `shipgate status`/`shipgate report` read only what is
already recorded in a project's ledger. Neither one ever opens `.claude/settings.json`
or touches the hook wiring that is supposed to *produce* new ledger records. The
result, confirmed directly: `shipgate status --project-dir .` printed `GATE: GREEN`
for a project whose every hook (`PreToolUse`/`PostToolUse`/`Stop`) pointed at an
interpreter path that no longer existed on disk — hooks fail open by design (they
must never crash a Claude Code session visibly), so the hook simply never ran, and
`status` had no way to know that, because it never looked. A check that cannot fail
when the thing it checks is broken is not a check.

This module is the missing check: it inspects `.claude/settings.json` directly for
each shipgate-managed hook entry (identified by `-m shipgate.hooks.<name>` in the
command, so a project's own unrelated hooks under the same event name are left
alone), and for each one confirms — for real, not by trusting the path string —
that the configured interpreter file exists and that the hook module actually
imports under it (`<interpreter> -c "import shipgate.hooks.<name>"`, run as a real
subprocess). It also checks whether a hook has ever genuinely fired — **not** by
reading the ledger file's own last-write time (Session 046: found live, against
a pilot project's own ledger, that a CLI-driven write — e.g. `shipgate
declare-task-class` — makes the file mtime look fresh on a day no hook fired at
all), but by querying the ledger's own `events` table for the most recent row whose
`record_type` is one only a real hook writes (`pretooluse_hook`/`posttooluse_hook`/
`stop_hook`). The import-probe above proves a hook *could* run; this is the only
signal that proves one *has*.

**Severity is deliberately two-tier, not one hard pass/fail**, matching the
distinction the gate itself draws between hard-block and advisory: a dead
interpreter or a module that fails to import is a `fail` — that hook is definitely
not recording, full stop. A stale ledger mtime is only a `warn` — a project that
genuinely hasn't been touched in days is not broken, and inventing a hard threshold
for "how long is too long" would be exactly the kind of confident-but-ungrounded
claim this project's own doctrine forbids. Read the warning, don't treat it as a
verdict on its own.

Zero Claude-Code-specific imports beyond string-matching the `shipgate.hooks.*`
module naming convention already used by `shipgate/discipline/templates.py`, which
writes these same command strings — this module reads that convention back, it
does not invent a new one.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_HOOK_EVENTS = ("PreToolUse", "PostToolUse", "Stop")
# The exact invocation shape `shipgate/discipline/templates.py` writes:
# "<python path> -m shipgate.hooks.<name>". Matching on this substring is how a
# project's own unrelated hooks under the same event name (e.g. a repo's own lint
# pre-commit hook) are told apart from ShipGate's own entries.
_MODULE_PREFIX = "-m shipgate.hooks."

#: Ledger silence beyond this many days renders as a warning, not a failure — see
#: the module docstring for why this can never be a hard fail.
_STALE_LEDGER_WARN_DAYS = 3.0

#: Session 046 (pilot-supervisor, evidence-framing finding): the ledger
#: freshness check below used to read `.shipgate/ledger.db`'s own file mtime -- which
#: reflects the most recent write from *any* source, including CLI-driven ones
#: (`shipgate declare-task-class` writes a `high_risk_change` event; the gate
#: orchestrator writes `gate_evaluation`). Demonstrated live against a pilot project's own
#: ledger: a CLI-only write from 2026-09-07 made the file mtime look under a day old,
#: while the most recent row an actual Claude Code hook ever wrote was still
#: 2026-08-19 -- the exact false-fresh reading that caused a real, reported confusion.
#: Matches `shipgate.gate.checkers`'s own `_GENUINE_OBSERVED_RECORD_TYPES` allowlist
#: (duplicated, not imported -- that name is private to its own module, same
#: don't-reach-into-another-package's-internals convention `shipgate.discipline.session`
#: already applies to `shipgate.hooks._common`).
_GENUINE_LIVE_HOOK_RECORD_TYPES = ("pretooluse_hook", "posttooluse_hook", "stop_hook")


@dataclass(frozen=True)
class HookWiringIssue:
    event: str  # hook event name, or "*" for a settings.json-level or ledger-level issue
    severity: str  # "fail" or "warn" — see module docstring
    reason: str


@dataclass(frozen=True)
class WiringReport:
    settings_path: Path
    hooks_found: tuple[str, ...]  # event names that had a shipgate-managed hook entry
    issues: tuple[HookWiringIssue, ...]
    ledger_path: Path | None  # None if the ledger file does not exist
    ledger_last_write: float | None  # epoch seconds; None if ledger absent
    last_genuine_hook_fire: str | None  # ISO timestamp of the most recent row whose
    # record_type is in _GENUINE_LIVE_HOOK_RECORD_TYPES -- None if no such row has ever
    # existed, regardless of how recently the ledger file itself was written by
    # something else (a CLI command). This is the field that answers "has a hook ever
    # actually fired here", which `ledger_last_write` (file mtime) cannot.

    @property
    def is_vacuous(self) -> bool:
        """True when `.claude/settings.json` has no shipgate-managed hook entries at
        all — this is not "healthy": it means `shipgate init` was never run here (or
        a `settings.json` from something unrelated is present), so there is nothing
        for this check to have verified. Same vacuous-pass discipline `shipgate
        doctor`'s existing shipfile check already applies to itself — never rendered
        as a pass on zero observations."""
        return not self.hooks_found

    @property
    def has_failures(self) -> bool:
        return any(issue.severity == "fail" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warn" for issue in self.issues)


def _extract_shipgate_commands(settings: dict) -> dict[str, str]:
    """event name -> raw command string, for each hook entry whose command invokes
    a `shipgate.hooks` module. A project can have other, unrelated hooks under the
    same event name (matcher-scoped, e.g. `Bash|PowerShell`) — those are left alone;
    only entries carrying the ShipGate module invocation are ours to check."""
    found: dict[str, str] = {}
    hooks = settings.get("hooks", {})
    for event in _HOOK_EVENTS:
        for matcher_block in hooks.get(event, []):
            for entry in matcher_block.get("hooks", []):
                command = entry.get("command", "")
                if _MODULE_PREFIX in command:
                    found[event] = command
    return found


def _interpreter_and_module(command: str) -> tuple[str, str] | None:
    idx = command.find(_MODULE_PREFIX)
    if idx == -1:
        return None
    interpreter = command[:idx].strip().strip('"')
    rest = command[idx + len("-m "):]
    module = rest.split()[0] if rest.split() else ""
    if not interpreter or not module:
        return None
    return interpreter, module


def _locate_git_bash() -> str | None:
    """**Third finding while building this check, 2026-09-21: `shutil.which("bash")` is
    not safe to trust either, and not for the reason first suspected.** The first
    attempt used a bare `["bash", "-c", ...]`; that resolved to `System32\\bash.exe`
    (the WSL launcher) because Windows's `CreateProcess` search order checks the system
    directory before `PATH`. Switching to `shutil.which("bash")` (which walks `PATH`
    directly, like `where bash`) was assumed to fix it — **it did not, reproduced live
    a second way**: this machine's actual System/User `PATH` environment variable (the
    one a real subprocess, and presumably a real Claude Code hook subprocess, inherits)
    has no Git `bin`/`usr\\bin` directory on it at all — only `Git\\cmd`, which holds
    `git.exe` but not `bash.exe`. So `shutil.which("bash")` finds *only*
    `System32\\bash.exe` there too. This would have made every correctly-quoted,
    correctly-working hook on this machine report a false failure — precisely the
    "blocks wrongly" class of defect this project treats as worse than not checking at
    all. (It appeared to work during this function's first draft only because it was
    tested from inside a Git-Bash-hosted shell, whose own internal `PATH` prepends
    Git's `bin` directories ahead of `System32` — not representative of the real
    environment a hook subprocess runs in.)

    The fix: `git.exe` genuinely is on `PATH` (confirmed: `shutil.which("git")`
    resolves it), so `git --exec-path` is used to derive the real Git-for-Windows
    install root independent of `PATH` ordering — the same technique other Windows dev
    tools (e.g. VS Code) use to locate Git Bash reliably. Returns `None`, never a guess,
    if git itself isn't found or the derived `bash.exe` doesn't exist — callers must
    treat that as "could not verify," not as a failure of the hook being checked."""
    git_path = shutil.which("git")
    if git_path is None:
        return None
    try:
        result = subprocess.run(
            [git_path, "--exec-path"], capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # `git --exec-path` -> "<git root>/mingw64/libexec/git-core"; three levels up is
    # the Git-for-Windows install root, which holds bin/bash.exe and usr/bin/bash.exe
    # (either is a real, working Git Bash — checked in order, first hit wins).
    git_root = Path(result.stdout.strip()).parent.parent.parent
    for candidate in (git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def _probe_command_through_shell(command: str, scratch_cwd: Path, bash_path: str) -> str | None:
    """**Root-cause finding, 2026-09-21 (pilot project), P0: `_interpreter_and_module`
    above proves an interpreter exists and a module imports — it has never once run the
    configured command *string*.** Claude Code invokes hook commands through Git Bash on
    Windows; an unquoted interpreter path containing a space (every real interpreter
    path in this fleet) fails at the shell with `command not found` (exit 127) before
    Python is ever reached, and every hook fails open by design, so `doctor` stayed
    green while 13 fleet projects silently failed since install. This function is the
    missing check: it runs the *exact* configured string through the real Git Bash (see
    `_locate_git_bash`), the same way Claude Code does, and treats a nonzero exit as a
    real, unconditional failure — independent of, and in addition to,
    `_interpreter_and_module`'s argv-parse check (kept as its own separate check, not
    replaced by this one).

    Runs against `scratch_cwd`, never the real project directory: the hook payload's
    own `cwd` field is what `shipgate.hooks._common.open_project_ledger` trusts when
    deciding where to write `.shipgate/ledger.db`, so pointing the synthetic payload's
    `cwd` at a throwaway temp directory means this probe can never write to — or be
    confused with — the real project's ledger. Every hook entrypoint's `run()` has a
    documented never-raises, fail-open contract for a normal payload (`session_id` +
    `cwd` is enough; `Stop` with no `shipfile.yaml` in `scratch_cwd` just writes its
    observational event and returns), so a bare two-field payload probes the real
    invocation path without ever reaching gate evaluation.

    `bash_path` is resolved once by the caller (`_locate_git_bash`), not per call — see
    that function's docstring for why a lesser resolution (a bare `bash` off `PATH`)
    was rejected as unsafe. Returns `None` on success, or a human-readable failure
    description."""
    payload = json.dumps({
        "session_id": f"shipgate-doctor-shell-probe-{uuid.uuid4()}",
        "cwd": str(scratch_cwd),
    })
    try:
        probe = subprocess.run(
            [bash_path, "-c", command],
            input=payload, capture_output=True, text=True, timeout=30, check=False,
        )
    except OSError as exc:
        return f"could not run the configured command through bash ({bash_path}) at all: {exc}"
    except subprocess.SubprocessError as exc:
        return f"could not run the configured command through bash at all: {exc}"

    if probe.returncode != 0:
        stderr_tail = probe.stderr.strip()[-300:] or "(no stderr)"
        return (
            f"the configured command failed when actually executed through `bash -c`, the "
            f"same way Claude Code invokes it on Windows (exit {probe.returncode}) — this "
            f"hook is NOT firing, regardless of what the interpreter/import checks above "
            f"found: {stderr_tail}"
        )
    return None


def _most_recent_genuine_hook_fire(ledger_path: Path) -> str | None:
    """The ISO `timestamp` of the most recent event row whose `record_type` is in
    `_GENUINE_LIVE_HOOK_RECORD_TYPES` -- `None` if no such row has ever existed. Opened
    via a SQLite `mode=ro` URI (refused at the SQLite/OS level for any write, the same
    hard constraint `shipgate.analyze.data.open_ledger_readonly` uses -- duplicated here
    rather than imported, since this module has no other reason to depend on
    `shipgate.analyze`, an unrelated, later-layered addition). A read that fails for any
    reason (corrupt/locked/unreadable ledger) returns `None` rather than raising --
    this is a best-effort freshness signal, not the hash-chain integrity check
    `shipgate.analyze` itself performs before trusting a ledger for real."""
    try:
        uri = ledger_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            placeholders = ",".join("?" for _ in _GENUINE_LIVE_HOOK_RECORD_TYPES)
            row = conn.execute(
                f"SELECT MAX(timestamp) FROM events WHERE record_type IN ({placeholders})",
                _GENUINE_LIVE_HOOK_RECORD_TYPES,
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def check_hook_wiring(project_dir: Path) -> WiringReport:
    """Read-only. Never edits `.claude/settings.json`, never writes to the ledger."""
    settings_path = project_dir / ".claude" / "settings.json"

    if not settings_path.exists():
        return WiringReport(
            settings_path=settings_path, hooks_found=(), issues=(),
            ledger_path=None, ledger_last_write=None, last_genuine_hook_fire=None,
        )

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return WiringReport(
            settings_path=settings_path,
            hooks_found=(),
            issues=(HookWiringIssue("*", "fail", f"{settings_path} unreadable or invalid JSON: {exc}"),),
            ledger_path=None, ledger_last_write=None, last_genuine_hook_fire=None,
        )

    commands = _extract_shipgate_commands(settings)
    issues: list[HookWiringIssue] = []

    for event, command in sorted(commands.items()):
        parsed = _interpreter_and_module(command)
        if parsed is None:
            issues.append(HookWiringIssue(
                event, "fail",
                f"could not parse an interpreter + `shipgate.hooks.<name>` module out of the "
                f"configured command: {command!r}",
            ))
            continue

        interpreter, module = parsed
        if not Path(interpreter).is_file():
            issues.append(HookWiringIssue(
                event, "fail",
                f"configured interpreter does not exist on disk: {interpreter} — this hook has "
                f"been silently failing open (hooks fail open by design, never crash visibly "
                f"back to Claude Code) since whenever that path stopped resolving. Two ways to "
                f"fix it, either is fine: (1) fastest — edit {project_dir / '.claude' / 'settings.json'} "
                f"directly and replace every dead interpreter path with the real one, "
                f"{sys.executable!r}; (2) `shipgate init --project-dir . --intent-summary "
                f'"<one line>" --test-command "<your test command>"` also self-heals it, but '
                f"**only with both flags supplied on the command line** — the bare form "
                f"`shipgate init --project-dir .` prompts interactively for those two values and "
                f"aborts immediately on a null/non-interactive stdin (found live, Session 044, "
                f"F-1: this is exactly the shape of failure a scripted or agent-driven repair "
                f"hits, not a human at a keyboard).",
            ))
            continue

        try:
            probe = subprocess.run(
                [interpreter, "-c", f"import {module}"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            issues.append(HookWiringIssue(
                event, "fail",
                f"could not execute the configured interpreter ({interpreter}) at all: {exc}",
            ))
            continue

        if probe.returncode != 0:
            stderr_tail = probe.stderr.strip()[-300:]
            issues.append(HookWiringIssue(
                event, "fail",
                f"`{interpreter} -c \"import {module}\"` failed (exit {probe.returncode}): "
                f"{stderr_tail}",
            ))

    # A second, separate check from the argv-parse checks above — see
    # `_probe_command_through_shell`'s docstring for why the two are not redundant.
    # Runs unconditionally for every configured command, even one whose argv-parse
    # checks above already passed: that combination (argv check green, shell check red)
    # is exactly the unquoted-interpreter-path bug this check exists to catch.
    if commands:
        bash_path = _locate_git_bash()
        if bash_path is None:
            # Honest "could not verify," never silently treated as a pass and never
            # manufactured into a "fail" against the hook itself — see
            # `_locate_git_bash`'s docstring for why a lesser resolution was rejected.
            issues.append(HookWiringIssue(
                "*", "warn",
                "could not locate a real Git Bash install (via `git --exec-path`) to run the "
                "real shell-execution check with — skipping it; the interpreter/import checks "
                "above still ran. Install Git for Windows, or ensure `git` resolves on PATH.",
            ))
        else:
            with tempfile.TemporaryDirectory(prefix="shipgate-doctor-shell-probe-") as scratch_dir:
                scratch_path = Path(scratch_dir)
                for event, command in sorted(commands.items()):
                    shell_error = _probe_command_through_shell(command, scratch_path, bash_path)
                    if shell_error is not None:
                        issues.append(HookWiringIssue(event, "fail", shell_error))

    ledger_path = project_dir / ".shipgate" / "ledger.db"
    ledger_exists = ledger_path.exists()
    ledger_mtime = ledger_path.stat().st_mtime if ledger_exists else None
    last_fire_iso = _most_recent_genuine_hook_fire(ledger_path) if ledger_exists else None

    if commands and not ledger_exists:
        issues.append(HookWiringIssue(
            "*", "warn",
            "hooks are configured but .shipgate/ledger.db does not exist yet — expected before "
            "the first tool call or session Stop in this project; a problem if you have already "
            "worked here since installing.",
        ))
    elif commands and last_fire_iso is None:
        # The ledger exists and has been written to (ledger_mtime proves that), but never
        # by an actual hook -- every row so far came from a CLI command instead (e.g.
        # `shipgate declare-task-class`, or the gate orchestrator running from the CLI).
        # This is deliberately its own distinct message, not folded into the staleness
        # branch below: file mtime alone would have called this "fresh" the same day a
        # CLI-only write happened, which is exactly the false-fresh reading this session
        # demonstrated live against a pilot project's own ledger (see module docstring).
        issues.append(HookWiringIssue(
            "*", "warn",
            "the interpreter resolves and the hook module imports (that proves the hook COULD "
            "run) — but no row in .shipgate/ledger.db has ever come from an actual hook firing; "
            "every row so far is CLI-sourced. This does not prove the hooks work end-to-end. "
            "Work normally in Claude Code, then re-run `doctor`: you should see this warning "
            "replaced by a genuine last-fired timestamp. If it stays, or if you've clearly been "
            "working here already, treat this as a live problem, not a formality.",
        ))
    elif commands and last_fire_iso is not None:
        try:
            last_fire_epoch = datetime.fromisoformat(last_fire_iso).timestamp()
            age_days = (time.time() - last_fire_epoch) / 86400
        except ValueError:
            age_days = None
        if age_days is not None and age_days > _STALE_LEDGER_WARN_DAYS:
            issues.append(HookWiringIssue(
                "*", "warn",
                f"the most recent row an actual hook wrote (not just any ledger activity — CLI "
                f"commands write rows too, and don't count here) was {age_days:.1f} day(s) ago, "
                f"at {last_fire_iso}. If you have been actively working in this project since "
                f"then, the hooks may not actually be firing despite looking configured — worth "
                f"a closer look. If you genuinely haven't worked here recently, this is expected "
                f"and not a problem.",
            ))

    return WiringReport(
        settings_path=settings_path,
        hooks_found=tuple(sorted(commands)),
        issues=tuple(issues),
        ledger_path=ledger_path if ledger_exists else None,
        ledger_last_write=ledger_mtime,
        last_genuine_hook_fire=last_fire_iso,
    )


__all__ = ["HookWiringIssue", "WiringReport", "check_hook_wiring"]
