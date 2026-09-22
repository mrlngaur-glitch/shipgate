"""Session 042 — `shipgate.doctor.wiring.check_hook_wiring`. Fabricates
`.claude/settings.json` directly (same convention as `test_doctor.py`'s
`Shipfile(raw={...})`: no round trip through `shipgate init`) so each scenario is
exact and self-contained, and proves the exact live bug this module exists to catch:
a dead interpreter path that `shipgate status`/`report` have no way to see.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shipgate.discipline.session import open_project_ledger
from shipgate.doctor.wiring import check_hook_wiring
from shipgate.hooks._common import ensure_session


def _seed_genuine_hook_fire(project_dir: Path, *, age_days: float = 0.0) -> None:
    """Writes one real, valid ledger row with a record_type in
    `_GENUINE_LIVE_HOOK_RECORD_TYPES`, `age_days` ago -- the only way to test the
    freshness check's real branches, since Session 046 made it query actual event rows
    rather than trust the ledger file's own mtime (see wiring.py's module comment on
    `_GENUINE_LIVE_HOOK_RECORD_TYPES` for why an empty/fake file no longer suffices)."""
    ts = (datetime.now(UTC) - timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open_project_ledger(project_dir) as writer:
        ensure_session(writer, session_id="s1", cwd=str(project_dir))
        writer.insert_event(
            session_id="s1", source_file="<live-hook:PreToolUse>", source_offset=0,
            transcript_tier="session", record_type="pretooluse_hook", timestamp=ts,
        )


def _write_settings(project_dir: Path, interpreter: str, module: str = "shipgate.hooks.pretooluse") -> None:
    settings_dir = project_dir / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": f'"{interpreter}" -m {module}'}],
                }
            ]
        }
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def test_no_settings_json_is_vacuous(tmp_path: Path):
    report = check_hook_wiring(tmp_path)

    assert report.is_vacuous
    assert not report.issues


def test_settings_json_with_no_shipgate_hooks_is_vacuous(tmp_path: Path):
    """A project's own unrelated hooks (e.g. a lint pre-commit hook) under the same
    event name must not be mistaken for a ShipGate hook entry."""
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir(parents=True)
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "*", "hooks": [{"type": "command", "command": "python lint.py"}]}
            ]
        }
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    report = check_hook_wiring(tmp_path)

    assert report.is_vacuous


def test_dead_interpreter_path_is_a_failure_not_a_silent_pass(tmp_path: Path):
    """The exact live bug this module was built to catch (Session 042, fleet-rollout
    D-2/D-3): a configured interpreter that no longer exists on disk. This is what
    `shipgate status` could not detect."""
    _write_settings(tmp_path, r"C:\does\not\exist\python.exe")

    report = check_hook_wiring(tmp_path)

    assert not report.is_vacuous
    assert report.has_failures
    assert any("does not exist on disk" in issue.reason for issue in report.issues)


def test_dead_interpreter_remedy_gives_a_non_interactive_path(tmp_path: Path):
    """F-1 (Session 044, a pilot project's own analyst): the remedy text used to say only
    `shipgate init --project-dir .` -- which prompts interactively for two values and
    aborts on null stdin, exactly the environment every non-human analyst runs in. The
    remedy must name a path that actually works unattended."""
    _write_settings(tmp_path, r"C:\does\not\exist\python.exe")

    report = check_hook_wiring(tmp_path)

    reason = next(issue.reason for issue in report.issues if issue.severity == "fail")
    assert "--intent-summary" in reason and "--test-command" in reason
    assert "settings.json" in reason  # the direct-edit alternative is also offered
    assert "prompts interactively" in reason  # warns against the bare, hanging form


def test_real_interpreter_with_importable_module_and_a_recent_genuine_fire_is_ok(tmp_path: Path):
    _write_settings(tmp_path, sys.executable)
    _seed_genuine_hook_fire(tmp_path, age_days=0.0)

    report = check_hook_wiring(tmp_path)

    assert not report.is_vacuous
    assert not report.has_failures
    assert not report.has_warnings
    assert report.hooks_found == ("PreToolUse",)
    assert report.last_genuine_hook_fire is not None


def test_interpreter_exists_but_module_does_not_import_is_a_failure(tmp_path: Path):
    _write_settings(tmp_path, sys.executable, module="shipgate.hooks.no_such_hook_module")

    report = check_hook_wiring(tmp_path)

    assert report.has_failures
    assert any("failed" in issue.reason for issue in report.issues)


def test_missing_ledger_with_configured_hooks_is_a_warning_not_a_failure(tmp_path: Path):
    """Expected right after a fresh `shipgate init`, before the first tool call —
    must not be reported as broken."""
    _write_settings(tmp_path, sys.executable)

    report = check_hook_wiring(tmp_path)

    assert not report.has_failures
    assert report.has_warnings
    assert any("does not exist yet" in issue.reason for issue in report.issues)


def test_stale_genuine_hook_fire_is_a_warning_not_a_hard_failure(tmp_path: Path):
    """A dormant-but-fine project must not be told it's broken — see the module
    docstring for why this can never be a hard fail."""
    _write_settings(tmp_path, sys.executable)
    _seed_genuine_hook_fire(tmp_path, age_days=10.0)

    report = check_hook_wiring(tmp_path)

    assert not report.has_failures
    assert report.has_warnings
    assert any("day(s) ago" in issue.reason for issue in report.issues)
    assert any("actual hook wrote" in issue.reason for issue in report.issues)


def test_ledger_active_but_never_from_a_real_hook_is_its_own_distinct_warning(tmp_path: Path):
    """Session 046 (pilot-supervisor, evidence-framing finding): demonstrated
    live against a pilot project's own ledger that a CLI-only write (e.g.
    `shipgate declare-task-class`) makes the ledger file's own mtime look fresh even
    though no hook has ever actually fired -- the exact false-fresh reading that caused
    a real, reported confusion. A ledger that has real rows, but none of them genuinely
    hook-sourced, must render its own distinct message, not the "fresh" silence a naive
    file-mtime check would give it."""
    _write_settings(tmp_path, sys.executable)
    with open_project_ledger(tmp_path) as writer:
        ensure_session(writer, session_id="s1", cwd=str(tmp_path))
        writer.insert_event(
            session_id="s1", source_file="<blast-radius-counter>", source_offset=0,
            transcript_tier="session", record_type="high_risk_change",
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    report = check_hook_wiring(tmp_path)

    assert not report.has_failures
    assert report.has_warnings
    assert report.last_genuine_hook_fire is None
    assert any("has ever come from an actual hook firing" in issue.reason for issue in report.issues)
    assert not any("day(s) ago" in issue.reason for issue in report.issues)  # not the staleness branch


# --- Session 048 (pilot project, 2026-09-21, P0): the real shell-execution check ---------
#
# `_write_settings` above always writes a QUOTED command, because it was written to
# exercise `_interpreter_and_module`'s argv-parse check, which only ever proved an
# interpreter exists and a module imports -- never that the configured *string* runs.
# These tests write the raw, unquoted command shape every one of the 13 fleet projects
# actually had, and prove `doctor` now catches it via a real `bash -c` execution, not
# just the argv-parse check.


def _write_raw_settings(project_dir: Path, command: str, *, event: str = "PreToolUse") -> None:
    settings_dir = project_dir / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "hooks": {event: [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]}
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _interpreter_that_breaks_unquoted(tmp_path: Path) -> str:
    """A real, existing interpreter path that any POSIX-style shell mangles when unquoted.

    On Windows `sys.executable` already is one (backslashes are stripped as escapes --
    see the next test's docstring). **Found by the first public CI run, 2026-09-22
    (Linux):** a POSIX `sys.executable` (e.g. `/opt/.../bin/python`) has neither a space
    nor a backslash, runs fine unquoted, and silently made the tests below assert a
    Windows-only fact. There, a symlink to the real interpreter is placed under a
    directory whose name contains a space -- the original live bug's shape -- so the
    unquoted form fails at the shell on every platform, for the same reason."""
    if sys.platform == "win32":
        return sys.executable
    spaced_dir = tmp_path / "dir with space"
    spaced_dir.mkdir()
    link = spaced_dir / "python"
    link.symlink_to(sys.executable)
    return str(link)


def test_unquoted_interpreter_path_with_a_space_is_caught_by_the_real_shell_check(tmp_path: Path):
    """The live bug (Session 048, pilot project, 2026-09-21), reproduced with the real
    interpreter this test process runs under. Claude Code runs hook commands through
    Git Bash on Windows, where an unquoted Windows path fails at the shell before
    Python is ever reached -- and not only when it contains a space.

    **Correction, Session 049:** the original version of this test asserted
    `" " in sys.executable` and skipped itself as meaningless otherwise -- true only
    by the accident of `shipgate_private`'s own path, and it silently stopped
    verifying anything the moment this exact test ran against a `git clone` into a
    space-free temp directory (this project's own owed clean-clone verification step,
    which is what caught it). Reproduced directly: bash's unquoted-word handling
    doesn't just word-split on spaces, it also strips bare backslashes as escape
    characters, so `C:\\Users\\USER\\...\\python.exe` (no space at all) still comes out
    the other side as `C:UsersUSERpython.exe` and fails with `command not found` --
    the same exit 127, for a more general reason than the original diagnosis named.
    Any real absolute Windows interpreter path reproduces it, with or without a space,
    which is also why quoting (the actual fix) needed no space-specific logic. The old
    argv-parse check (`_interpreter_and_module`) could not catch either shape: it never
    executes anything through a shell, only stats the interpreter file and runs it
    directly as an argv list."""
    unquoted_command = f"{_interpreter_that_breaks_unquoted(tmp_path)} -m shipgate.hooks.pretooluse"

    _write_raw_settings(tmp_path, unquoted_command)
    report = check_hook_wiring(tmp_path)

    assert report.has_failures
    assert any(
        "actually executed through" in issue.reason and "NOT firing" in issue.reason
        for issue in report.issues
    )


def test_same_path_quoted_passes_the_real_shell_check(tmp_path: Path):
    """The fix: the identical interpreter path from the previous test (space AND
    backslashes, the real Windows shape), quoted -- must render clean through the real
    shell check and must genuinely run the module (not merely avoid the word-split
    error)."""
    quoted_command = f'"{sys.executable}" -m shipgate.hooks.pretooluse'

    _write_raw_settings(tmp_path, quoted_command)
    report = check_hook_wiring(tmp_path)

    assert not report.has_failures
    assert not any("actually executed through" in issue.reason for issue in report.issues)


def test_doctor_red_then_green_after_shipgate_init_repairs_a_stale_unquoted_entry(tmp_path: Path):
    """End-to-end proof of the actual repair path (no new `--repair-hooks` flag needed
    -- `init`'s existing suffix-matched merge already overwrites a stale `command` in
    place): a project with today's live bug shape goes doctor-red, `shipgate init`
    re-run repairs it in place, and the identical project goes doctor-green."""
    from shipgate.discipline.init import run_init

    unquoted_command = f"{_interpreter_that_breaks_unquoted(tmp_path)} -m shipgate.hooks.pretooluse"
    _write_raw_settings(tmp_path, unquoted_command)

    red = check_hook_wiring(tmp_path)
    assert red.has_failures

    run_init(tmp_path, intent_summary="x", test_command="pytest -q")

    green = check_hook_wiring(tmp_path)
    assert not green.has_failures
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    repaired_command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert repaired_command.startswith('"'), "init must repair the stale entry to a quoted command"


def test_unreadable_settings_json_is_a_failure(tmp_path: Path):
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text("{not valid json", encoding="utf-8")

    report = check_hook_wiring(tmp_path)

    assert report.has_failures
    assert any("invalid JSON" in issue.reason for issue in report.issues)
