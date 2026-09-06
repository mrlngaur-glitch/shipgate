"""Task 2.2 — deterministic checkers (`tests_pass`, `file_exists`,
`forbidden_pattern_absent`, `command_succeeds`), each with vacuous-pass detection built
in where the concept applies (task 2.5, built in from the start — see
`shipgate/gate/checkers.py`'s module docstring for the per-checker audit of where it
does and honestly doesn't).

Every test runs a REAL check against a REAL `tmp_path` project — real subprocesses for
`tests_pass`/`command_succeeds`, a real filesystem walk for `file_exists`/
`forbidden_pattern_absent`. Standing note (Session 004 close): the
messy realistic case, not just the clean isolated one — several tests below combine a
condition with something a real project would actually have (a vendor directory full of
noise, a nested match, an ignored-directory false-negative risk) rather than a single
minimal fixture.
"""

import os
import sqlite3
import sys
import venv
from pathlib import Path

import pytest

from shipgate.gate.checkers import (
    DEFAULT_CHECKER_TIMEOUT_SECONDS,
    _pytest_extra_args,
    _run_shell_command_bounded,
    check_command_succeeds,
    check_file_exists,
    check_forbidden_pattern_absent,
    check_inventory_complete,
    check_runtime_evidence,
    check_tests_pass,
    run_checker,
)
from shipgate.ledger.writer import LedgerWriter
from shipgate.verdicts import EvidenceTier, Verdict

PY = sys.executable


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def no_pytest_venv_python(tmp_path_factory) -> str:
    """A real, freshly built venv with no `pytest` installed -- `venv.create(...,
    with_pip=False)` never installs anything beyond the stdlib, so this is genuinely,
    not simulated, the exact state every one of this project's real pilot venvs
    was actually found to be in. Module-scoped: built once
    (~0.3s), reused by every test below that needs it, matching
    `test_docs_reality.py`'s own module-scope fixture pattern for an expensive-ish
    real setup shared across several tests."""
    target = tmp_path_factory.mktemp("no_pytest_venv") / "venv"
    venv.create(target, with_pip=False)
    py = target / "Scripts" / "python.exe" if os.name == "nt" else target / "bin" / "python"
    assert py.exists(), f"venv.create did not produce an interpreter at {py}"
    return str(py)


# --- tests_pass -------------------------------------------------------------------------


def test_pytest_extra_args_recognizes_the_pilots_own_backslash_path_shape():
    """The Sixth founder finding's own root cause, isolated: `shlex.split`'s default
    POSIX mode treats `\\` as an escape character and mangles an unquoted Windows
    path before the recognizer's own allowlist ever runs -- reproduced directly
    against a real pilot project's own literal shipfile command shape."""
    assert _pytest_extra_args(r"venv\Scripts\python.exe -m pytest -q") == ["-q"]


def test_pytest_extra_args_recognizes_a_quoted_absolute_path_with_spaces():
    assert _pytest_extra_args(
        r'"C:\Some Directory\pilot-project\.venv\Scripts\python.exe" -m pytest -q'
    ) == ["-q"]


def test_pytest_extra_args_recognizes_forward_slash_and_bare_forms_unchanged():
    """Regression guard: the forms that already worked must keep working."""
    assert _pytest_extra_args("python -m pytest tests/") == ["tests/"]
    assert _pytest_extra_args("pytest -q -k test_keep") == ["-q", "-k", "test_keep"]
    assert _pytest_extra_args("python3.12 -m pytest") == []


def test_pytest_extra_args_still_rejects_a_genuinely_non_pytest_command():
    assert _pytest_extra_args("npm test") is None
    assert _pytest_extra_args(f'"{PY}" -c "import sys; sys.exit(0)"') is None


def test_tests_pass_pytest_command_all_passing_is_runtime_verified(tmp_path):
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED
    assert result.observed is True


def test_tests_pass_python_dash_m_pytest_form_is_recognized(tmp_path):
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "python -m pytest -q"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED


def test_tests_pass_a_real_failure_is_contradicted_not_verified(tmp_path):
    _write(tmp_path, "test_x.py", "def test_a():\n    assert 1 == 2\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED
    assert result.evidence_tier is None
    assert "1 failed" in result.reason


def test_tests_pass_zero_collected_is_the_vacuous_case_the_founders_scenario(tmp_path):
    """The exact false-completion shape the report names: an agent claims 'tests pass'
    but the suite collected nothing. Must never render VERIFIED."""
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False
    assert result.verdict != Verdict.VERIFIED


def test_tests_pass_non_pytest_command_success_is_unverified_not_a_pass(tmp_path):
    """Founder review finding, fixed: an exit-code-0 from an unrecognized runner used
    to render VERIFIED. It must not -- a bare exit code doesn't confirm tests ran."""
    command = f'"{PY}" -c "import sys; sys.exit(0)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.evidence_tier is None
    assert result.observed is False
    assert result.verdict != Verdict.VERIFIED


def test_tests_pass_non_pytest_runner_that_finds_nothing_and_exits_zero_is_the_founders_exact_case(tmp_path):
    """The founder's own adversarial reproduction: a command that prints 'No tests
    found' and exits 0 -- exactly what a default Jest config does against a directory
    with no matching test files. Must not render green."""
    command = f'"{PY}" -c "print(\'No tests found\'); import sys; sys.exit(0)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.verdict != Verdict.VERIFIED
    assert result.observed is False


def test_tests_pass_pytest_timeout_is_unverified_not_a_hang(tmp_path):
    """The retry-cap/loop-breaker (task 2.6) doesn't exist yet, but this checker must
    still never block forever -- a real subprocess that outlives a short timeout must
    come back as a verdict, not an uncaught exception or an infinite wait."""
    _write(tmp_path, "test_slow.py", "import time\n\ndef test_slow():\n    time.sleep(30)\n")
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q"}, tmp_path, timeout=1)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.observed is False
    assert "did not finish within 1s" in result.reason


def test_tests_pass_non_pytest_timeout_is_unverified_not_a_hang(tmp_path):
    command = f'"{PY}" -c "import time; time.sleep(30)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path, timeout=1)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.observed is False
    assert "did not finish within 1s" in result.reason


def test_tests_pass_non_pytest_command_failure_is_contradicted(tmp_path):
    command = f'"{PY}" -c "import sys; sys.exit(1)"'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED


def test_tests_pass_pilot_command_shape_with_pytest_absent_is_vacuous_not_contradicted(
    tmp_path, no_pytest_venv_python
):
    """The live defect, reproduced exactly: a real pilot project's own shipfile
    command shape (`venv\\Scripts\\python.exe -m pytest -q`) against a venv that
    genuinely has no pytest installed -- real, not simulated (see
    `no_pytest_venv_python`). Before the Sixth founder finding's fix, this command was
    never recognized as pytest at all (the tokenizer mangled it), fell to the bare
    exit-code fallback, and rendered CONTRADICTED on `pytest`'s own real exit 1 --
    every one of the pilots' recorded `contradicted` verdicts was this exact false
    accusation. Must render UNVERIFIED_VACUOUS: recognized as pytest, 0 collected
    (pytest itself never ran), the existing collected-count machinery's own honest
    verdict for "nothing was observed" -- never CONTRADICTED, an accusation this
    checker has no evidence for.

    **Founder correction, this session: against an EMPTY `tmp_path` (as this test
    originally stood), this assertion passes for the WRONG reason** -- an empty
    directory renders UNVERIFIED_VACUOUS under ANY interpreter, including
    `sys.executable` (which DOES have pytest), so this test alone never actually
    proved the named interpreter was the one that ran; it only proved "0 collected
    happens somehow." Left in place as the minimal reproduction of the pilots' own
    literal command shape, but see
    `test_tests_pass_named_interpreter_honored_pytest_absent_is_vacuous_not_a_false_green`
    below for the causally-correct version (a real test file present, so a
    wrongly-substituted interpreter WOULD render a false VERIFIED, not an
    accidentally-matching vacuous)."""
    command = f"{no_pytest_venv_python} -m pytest -q"
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.verdict != Verdict.CONTRADICTED
    assert result.observed is False


# --- Seventh founder finding: the named interpreter must be the one that actually
# runs -- the Sixth founder finding's own fix introduced a new false-GREEN by
# recognizing an explicit interpreter path and then silently substituting
# sys.executable anyway. See shipgate/gate/checkers.py's module docstring. -----------


def test_tests_pass_named_interpreter_honored_pytest_absent_is_vacuous_not_a_false_green(
    tmp_path, no_pytest_venv_python
):
    """The causally-correct version of the test above, built from the founder's own
    critique: `tmp_path` now has ONE real, real passing test file, so the directory is
    genuinely non-vacuous. Before this session's fix, `check_tests_pass` recognized
    this command as pytest-shaped (already fixed, Sixth founder finding) but then
    ignored the named interpreter entirely and ran `sys.executable` (this repo's OWN
    dev venv, which DOES have pytest) against `tmp_path` instead -- `sys.executable`
    CAN collect and pass the one real test here, so the OLD code rendered a false
    VERIFIED, crediting a genuinely pytest-less venv with a result that was actually
    this gate's own. Confirmed live, the founder's own reproduction: a real pilot
    project's own `tests_pass` command rendered `VERIFIED, 1 passed, 1 collected`
    against a nonexistent path -- the collected test was ShipGate's own suite. Honoring the
    named interpreter correctly must render UNVERIFIED_VACUOUS here: the real named
    interpreter genuinely ran, genuinely tried to import pytest, and genuinely found
    nothing to collect -- never VERIFIED (nothing was confirmed passing) and never
    CONTRADICTED (no failing test ever ran)."""
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    command = f"{no_pytest_venv_python} -m pytest -q"
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.verdict != Verdict.VERIFIED
    assert result.verdict != Verdict.CONTRADICTED
    assert result.observed is False


def test_tests_pass_named_interpreter_that_does_not_exist_is_unverified_the_founders_own_repro(tmp_path):
    """The founder's own literal reproduction, verbatim: a shipfile command naming an
    interpreter path that exists nowhere on this machine
    (`C:\\NO\\SUCH\\PATH\\python.exe -m pytest -q`, here built portably). Before this
    session's fix, the path was recognized as pytest-shaped and then silently
    discarded -- `check_tests_pass` ran `sys.executable` (this gate's OWN venv)
    against `tmp_path` regardless, crediting a path that was never actually invoked
    with a real, misattributed result. Must render UNVERIFIED: no subprocess for the
    nonexistent path should ever even be attempted, let alone credited with a
    verdict about tests that were never its own."""
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    nonexistent = str(Path("NO") / "SUCH" / "PATH" / "python.exe")
    command = f"{nonexistent} -m pytest -q"
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.verdict != Verdict.VERIFIED
    assert result.verdict != Verdict.CONTRADICTED
    assert result.observed is False
    assert "does not exist" in result.reason


def test_tests_pass_named_interpreter_reason_records_both_shipfile_command_and_what_actually_ran(tmp_path):
    """(c): the ledger's recorded evidence must name what actually ran, not just
    replay the shipfile's own literal text as if the two could never differ --
    checked here via `CheckResult.reason`, the string a caller persists into the
    ledger's `raw_payload`."""
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    command = f'"{PY}" -m pytest -q'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert f"shipfile command: {command!r}" in result.reason
    assert "via '" in result.reason
    assert PY in result.reason


def test_tests_pass_named_pytest_executable_path_is_invoked_directly_not_substituted(tmp_path):
    """The other recognized shape (`kind == "pytest_executable"`): a shipfile naming
    `pytest`'s own executable directly, not `python -m pytest`. Must also be honored
    -- resolved and invoked as-is, not silently rewritten into `sys.executable -m
    pytest` (which happens to produce the same practical result here since this IS
    the dev venv's own pytest, but for the wrong reason -- the recorded evidence must
    still name the path that was actually resolved and run, see module docstring)."""
    pytest_exe = Path(PY).parent / ("pytest.exe" if os.name == "nt" else "pytest")
    assert pytest_exe.is_file(), f"this repo's own dev venv should have {pytest_exe}"
    _write(tmp_path, "test_x.py", "def test_a():\n    assert True\n")
    command = f'"{pytest_exe}" -q'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert str(pytest_exe) in result.reason


def test_tests_pass_pilot_command_shape_with_a_real_failure_is_still_contradicted(tmp_path):
    """Negative control for the fix above: the recognition fix must not turn every
    pytest-shaped command into vacuous/unverified -- a REAL collected, REAL failing
    suite through this exact command shape must still render CONTRADICTED, same as
    the existing bare `pytest -q` case."""
    _write(tmp_path, "test_x.py", "def test_a():\n    assert 1 == 2\n")
    command = f'"{PY}" -m pytest -q'
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": command}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED
    assert "1 failed" in result.reason


def test_tests_pass_scopes_to_a_k_expression_consistently(tmp_path):
    _write(
        tmp_path,
        "test_x.py",
        "def test_keep():\n    assert True\n\ndef test_drop():\n    assert False\n",
    )
    result = check_tests_pass({"id": "c1", "type": "tests_pass", "command": "pytest -q -k test_keep"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert "1 passed, 1 collected" in result.reason


# --- file_exists -------------------------------------------------------------------------


def test_file_exists_true_is_disk_verified(tmp_path):
    _write(tmp_path, "SESSION_LOG.md", "hello")
    result = check_file_exists({"id": "c2", "type": "file_exists", "path": "SESSION_LOG.md"}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.DISK_VERIFIED


def test_file_exists_false_is_contradicted(tmp_path):
    result = check_file_exists({"id": "c2", "type": "file_exists", "path": "SESSION_LOG.md"}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED
    assert result.evidence_tier is None


def test_file_exists_nested_path(tmp_path):
    _write(tmp_path, "shipgate/gate/checkers.py", "# code")
    result = check_file_exists(
        {"id": "c2", "type": "file_exists", "path": "shipgate/gate/checkers.py"}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED


# --- forbidden_pattern_absent --------------------------------------------------------------


def test_forbidden_pattern_found_nested_is_contradicted_with_the_path_named(tmp_path):
    _write(tmp_path, "src/deep/nested/file.py", "x = 1\n# TODO: fix this\n")
    _write(tmp_path, "src/clean.py", "y = 2\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["src"]}, tmp_path
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "deep" in result.reason and "nested" in result.reason
    assert "src/deep/nested/file.py" in result.reason  # POSIX separators on every OS


def test_forbidden_pattern_absent_with_real_files_scanned_is_disk_verified(tmp_path):
    _write(tmp_path, "src/clean_a.py", "x = 1\n")
    _write(tmp_path, "src/clean_b.py", "y = 2\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["src"]}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.DISK_VERIFIED
    assert result.observed is True


def test_forbidden_pattern_absent_on_a_nonexistent_path_is_vacuous_not_a_pass(tmp_path):
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["does_not_exist"]}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False


def test_forbidden_pattern_absent_on_an_empty_directory_is_also_vacuous(tmp_path):
    (tmp_path / "src").mkdir()
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["src"]}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS


def test_forbidden_pattern_ignores_vendor_directories_without_becoming_falsely_vacuous(tmp_path):
    """The messy realistic case: a real project has noise directories (here, .venv)
    that must be excluded from the scan -- but a TODO planted ONLY inside .venv must
    not (a) be flagged, since vendor code isn't the target, and (b) must not make the
    checker fall back to 'scanned 0 files' just because the excluded files happened to
    be the only ones present with a match -- there's a real, non-vendor file too, and
    that's what should register as scanned."""
    _write(tmp_path, ".venv/lib/vendored.py", "# TODO: not ours to fix\n")
    _write(tmp_path, "src/real_code.py", "z = 3\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO", "paths": ["."]}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED  # the only real TODO is in an ignored dir
    assert result.observed is True


def test_forbidden_pattern_defaults_paths_to_project_root_when_omitted(tmp_path):
    _write(tmp_path, "anywhere.py", "# TODO later\n")
    result = check_forbidden_pattern_absent(
        {"id": "c3", "type": "forbidden_pattern_absent", "pattern": "TODO"}, tmp_path
    )
    assert result.verdict == Verdict.CONTRADICTED


# --- command_succeeds ----------------------------------------------------------------------


def test_command_succeeds_exit_zero_is_runtime_verified(tmp_path):
    command = f'"{PY}" -c "import sys; sys.exit(0)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED


def test_command_succeeds_nonzero_exit_is_contradicted(tmp_path):
    command = f'"{PY}" -c "import sys; sys.exit(1)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED


def test_command_succeeds_unresolvable_tool_is_unverified_not_contradicted_blocker_1(
    tmp_path, monkeypatch
):
    """The live defect, reproduced exactly: `ruff check .` with an empty `PATH` --
    precisely the shape of `PATH` inside a `Stop` hook launched via its
    `.claude/settings.json` absolute-interpreter command, never through an activated
    shell, when the linter lives only in a venv's own `Scripts`/`bin` directory.
    Confirmed directly this session: `returncode == 1`, stderr `"'ruff' is not
    recognized as an internal or external command..."` -- ruff itself is clean; the
    checker just couldn't find it. Must render UNVERIFIED, never CONTRADICTED."""
    monkeypatch.setenv("PATH", "")
    result = check_command_succeeds(
        {"id": "c4", "type": "command_succeeds", "command": "ruff check ."}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED
    assert result.verdict != Verdict.CONTRADICTED
    assert result.observed is False


def test_command_succeeds_a_real_resolvable_failure_is_still_contradicted(tmp_path):
    """Negative control for the fix above: a command that DOES resolve and exits
    nonzero for a real reason (not a not-found signal) must still render
    CONTRADICTED -- the fix must not swallow genuine failures into UNVERIFIED."""
    command = f'"{PY}" -c "import sys; sys.stderr.write(\'a real assertion failed\'); sys.exit(1)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert result.verdict == Verdict.CONTRADICTED


def test_command_succeeds_timeout_is_unverified_not_a_hang(tmp_path):
    command = f'"{PY}" -c "import time; time.sleep(30)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path, timeout=1)
    assert result.verdict == Verdict.UNVERIFIED
    assert result.evidence_tier is None
    assert result.observed is False
    assert "did not finish within 1s" in result.reason


def test_command_succeeds_runs_in_the_given_project_root(tmp_path):
    """Proves cwd is actually threaded through, not just accepted and ignored --
    the command writes a file relative to its own cwd; if this checker silently ran
    somewhere else, the file wouldn't land where the test looks for it."""
    command = f'"{PY}" -c "open(\'marker.txt\', \'w\').write(\'here\')"'
    check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "here"


# --- Finding 5 (a launch blocker): the host locale must never be able to
# destroy a checker's ability to read a child process's output --------------------------


def test_run_shell_command_bounded_survives_a_byte_the_host_locale_cannot_decode(tmp_path):
    """Direct, byte-for-byte reproduction of the founder's own finding: a command that
    writes a raw UTF-8 byte sequence the HOST machine's locale can't decode (nothing to
    do with what the command itself intended to print) used to kill
    `subprocess.communicate()`'s background reader thread silently -- the exception
    never reached the caller, and `proc.stdout` came back `None` instead of a string.
    `\\U0001F40D` (snake emoji) encodes to bytes cp1252 (this dev machine's real,
    unforced locale) cannot decode -- confirmed via direct interpreter inspection
    before this fix existed. Decoding explicitly as UTF-8 (rather than the host locale)
    doesn't just avoid crashing -- it decodes CORRECTLY, since the bytes really are
    valid UTF-8; the fix's `errors="replace"` never even has to fire for this exact
    byte sequence, which is the point (see the next test for genuinely malformed
    UTF-8, where "replace" is what actually saves the read)."""
    command = f'"{PY}" -c "import sys; sys.stdout.buffer.write(\'\\U0001F40D\'.encode(\'utf-8\')); sys.stdout.buffer.flush()"'
    proc = _run_shell_command_bounded(command, cwd=tmp_path, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout is not None, "the exact defect: used to be None, silently, with no exception reaching here"
    assert proc.stdout == "\U0001f40d"


def test_run_shell_command_bounded_survives_genuinely_malformed_utf8(tmp_path):
    """Not every undecodable byte is valid-UTF-8-that-the-host-locale-rejects -- a
    command could also emit genuinely malformed UTF-8 (a lone continuation byte, here).
    `errors="replace"` is what actually saves this read: never raises (unlike the old
    host-locale `"strict"` default), and never silently discards the evidence something
    was wrong the way `errors="ignore"` would -- the replacement character stays in the
    captured text rather than the byte vanishing without a trace."""
    command = f'"{PY}" -c "import sys; sys.stdout.buffer.write(b\\"ok \\" + bytes([0x80]) + b\\" ok\\"); sys.stdout.buffer.flush()"'
    proc = _run_shell_command_bounded(command, cwd=tmp_path, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout is not None
    assert proc.stdout == "ok \ufffd ok"


def test_command_succeeds_no_longer_leaks_an_unhandled_thread_exception_on_undecodable_output(tmp_path, recwarn):
    """Same undecodable-byte scenario, through the public checker this time.
    `command_succeeds` never reads stdout content for its own verdict (by design --
    see the module docstring's stated limitation), so the verdict itself was never
    false here -- but the reader thread still died with an unhandled exception on
    every such invocation, silently, visible only as raw noise on the real process
    stderr and invisible to anything ShipGate records. pytest's own thread-exception
    hook turns an unhandled thread exception into a `PytestUnhandledThreadException`
    warning; asserting none fired is the regression guard for this fix's hygiene half,
    independent of the verdict (which is asserted unchanged, not a gap being closed)."""
    command = f'"{PY}" -c "import sys; sys.stdout.buffer.write(\'\\U0001F40D\'.encode(\'utf-8\')); sys.exit(0)"'
    result = check_command_succeeds({"id": "c4", "type": "command_succeeds", "command": command}, tmp_path)
    assert result.verdict == Verdict.VERIFIED  # unchanged -- return-code-only by design, not a gap
    thread_warnings = [w for w in recwarn.list if "Thread" in w.category.__name__]
    assert not thread_warnings, [str(w.message) for w in thread_warnings]


# --- inventory_complete (task 2.3) --------------------------------------------------------


def test_inventory_complete_the_founders_exact_scenario_agent_lists_3_grep_finds_5(tmp_path):
    """report §2.2 D3, Gate B condition 3, verbatim: 'agent lists 3, grep
    finds 5 -> blocks'. Five real call sites planted; the agent's own claim lists three
    of them. Must be CONTRADICTED, not VERIFIED, and must name what's missing."""
    for i in range(5):
        _write(tmp_path, f"src/mod_{i}.py", "from shipgate.ledger.writer import LedgerWriter\n")

    real_matches = []
    for i in range(5):
        real_matches.append(f"src/mod_{i}.py:1")

    claimed_items = [{"match": m, "disposition": "updated"} for m in real_matches[:3]]  # agent only lists 3

    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated", "intentionally-unchanged"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "2 real match(es) not in the claimed inventory" in result.reason


def test_inventory_complete_full_enumeration_with_allowed_dispositions_is_disk_verified(tmp_path):
    _write(tmp_path, "src/mod_a.py", "from shipgate.ledger.writer import LedgerWriter\n")
    _write(tmp_path, "src/mod_b.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [
        {"match": "src/mod_a.py:1", "disposition": "updated"},
        {"match": "src/mod_b.py:1", "disposition": "intentionally-unchanged"},
    ]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated", "intentionally-unchanged"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.DISK_VERIFIED


def test_inventory_complete_a_fabricated_claimed_item_is_contradicted(tmp_path):
    """The agent claims a match that no real grep finds -- stale or fabricated, and
    must be caught, not just missing real matches."""
    _write(tmp_path, "src/mod_a.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [
        {"match": "src/mod_a.py:1", "disposition": "updated"},
        {"match": "src/mod_ghost.py:1", "disposition": "updated"},
    ]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "no real grep found" in result.reason


def test_inventory_complete_a_disallowed_disposition_is_contradicted(tmp_path):
    _write(tmp_path, "src/mod_a.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [{"match": "src/mod_a.py:1", "disposition": "will_fix_later"}]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated", "intentionally-unchanged"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert "disallowed disposition" in result.reason


def test_inventory_complete_match_strings_use_posix_separators_not_native_ones(tmp_path):
    """Regression guard: a bare str(Path) on Windows uses backslashes, which would make
    a real match silently unmatchable against a claimed_items list written with forward
    slashes (the realistic form -- and what this session's own first draft of these
    tests used, which is exactly what caught this). Match strings must be POSIX-style
    on every OS, matching the same convention shipgate/ledger/paths.py already uses for
    source_dir and for the identical reason."""
    _write(tmp_path, "src/nested/mod.py", "from shipgate.ledger.writer import LedgerWriter\n")
    claimed_items = [{"match": "src/nested/mod.py:1", "disposition": "updated"}]
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "from shipgate.ledger.writer import",
            "required_dispositions": ["updated"],
        },
        tmp_path,
        claimed_items,
    )
    assert result.verdict == Verdict.VERIFIED
    assert "\\" not in result.reason


def test_inventory_complete_zero_grep_matches_is_vacuous(tmp_path):
    result = check_inventory_complete(
        {
            "id": "inv1",
            "type": "inventory_complete",
            "grep_pattern": "this_pattern_matches_nothing_anywhere",
            "required_dispositions": ["updated"],
        },
        tmp_path,
        claimed_items=[],
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False


# --- runtime_evidence, log_marker method (task 2.4) ----------------------------------------


def _ledger_with_event(tmp_path: Path, payload: dict, record_type: str = "posttooluse_hook") -> Path:
    """`record_type` defaults to `posttooluse_hook` — a genuine, externally-observed
    event type (`_GENUINE_OBSERVED_RECORD_TYPES` in `checkers.py`), not the synthetic
    `"test_event"` this helper used before the P38 self-poisoning fix. That prior default
    made every positive-match test below pass for a reason production code can never
    actually produce (nothing in this project ever writes `record_type="test_event"`) --
    fixed so these tests exercise the same record types a real hook run produces."""
    db_path = tmp_path / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with LedgerWriter(db_path, corpus_root=tmp_path) as writer:
        writer.insert_session(session_id="s1", project_slug="proj", source_dir=".")
        writer.insert_event(
            session_id="s1",
            source_file="<test>",
            source_offset=0,
            transcript_tier="session",
            record_type=record_type,
            timestamp="2026-08-15T00:00:00Z",
            raw_payload=payload,
        )
    return db_path


def test_runtime_evidence_marker_found_in_a_real_recorded_event_is_runtime_verified(tmp_path):
    _ledger_with_event(tmp_path, {"note": "SHIPGATE_GATE_FIRED happened here"})
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "SHIPGATE_GATE_FIRED"},
        tmp_path,
    )
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED


def test_runtime_evidence_marker_in_source_but_never_run_is_contradicted_the_d1_lie(tmp_path):
    """The exact D1 scenario: code ON DISK contains the marker (an agent could grep the
    source and claim 'it's there, so it must have run'), but the ledger -- real recorded
    events -- never shows it firing. Must be CONTRADICTED, not VERIFIED: grepping source
    would be exactly the disk-only lie this checker exists to catch."""
    _write(tmp_path, "src/emitter.py", 'print("SHIPGATE_GATE_FIRED")  # the marker exists on disk\n')
    _ledger_with_event(tmp_path, {"note": "some unrelated event, the marker never actually fired"})

    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "SHIPGATE_GATE_FIRED"},
        tmp_path,
    )
    assert result.verdict == Verdict.CONTRADICTED
    assert result.verdict != Verdict.VERIFIED


def test_runtime_evidence_no_ledger_at_all_is_vacuous(tmp_path):
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "X"}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False


def test_runtime_evidence_ledger_exists_but_has_no_payload_events_is_vacuous(tmp_path):
    db_path = tmp_path / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with LedgerWriter(db_path, corpus_root=tmp_path) as writer:
        writer.insert_session(session_id="s1", project_slug="proj", source_dir=".")
        writer.insert_event(
            session_id="s1",
            source_file="<test>",
            source_offset=0,
            transcript_tier="session",
            record_type="test_event",
            timestamp="2026-08-15T00:00:00Z",
            raw_payload=None,
        )
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "X"}, tmp_path
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS


def test_runtime_evidence_unimplemented_method_raises_a_specific_error(tmp_path):
    with pytest.raises(ValueError, match="endpoint_probe"):
        check_runtime_evidence(
            {"id": "re1", "type": "runtime_evidence", "method": "endpoint_probe", "detail": "X"}, tmp_path
        )


def test_runtime_evidence_hash_chain_still_verifies_after_a_checker_read(tmp_path):
    """A checker only ever reads the ledger -- proves it doesn't accidentally write
    anything (which would corrupt the append-only chain the ledger's own triggers
    otherwise enforce on writes, but a raw SELECT bypasses the writer entirely)."""
    from shipgate.ledger.integrity import verify_all_chains

    db_path = _ledger_with_event(tmp_path, {"note": "SHIPGATE_GATE_FIRED"})
    check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "SHIPGATE_GATE_FIRED"},
        tmp_path,
    )
    conn = sqlite3.connect(db_path)
    verify_all_chains(conn)


# --- runtime_evidence self-poisoning fix (PHASE_PLAN.md P38, item 3) -----------------------
#
# A real, found-live false-GREEN: `check_runtime_evidence`'s own CONTRADICTED reason
# quotes the `detail` marker it searched for; that reason lands in a real
# `record_type='gate_evaluation'` event; an unfiltered second search then finds its own
# prior failure and renders a false VERIFIED. Fixed via an explicit allowlist
# (`_GENUINE_OBSERVED_RECORD_TYPES`) of record types that represent something actually,
# externally observed. Every test below writes real events through the same
# `LedgerWriter` production code uses, never a mock.


@pytest.mark.parametrize(
    "genuine_record_type",
    ["pretooluse_hook", "posttooluse_hook", "stop_hook"],
)
def test_runtime_evidence_each_genuine_record_type_still_counts_as_evidence(tmp_path, genuine_record_type):
    """Negative control, one per allowlisted type (the inventory rule -- enumerate every
    member of a claimed set, not just the one the default fixture happens to use):
    the fix must not have collapsed into a checker that never finds anything real."""
    _ledger_with_event(tmp_path, {"note": "MARKER_REAL"}, record_type=genuine_record_type)
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "MARKER_REAL"},
        tmp_path,
    )
    assert result.verdict == Verdict.VERIFIED
    assert result.evidence_tier == EvidenceTier.RUNTIME_VERIFIED


@pytest.mark.parametrize(
    "excluded_record_type,payload",
    [
        ("gate_evaluation", {"conditions": [{"reason": "marker 'MARKER_POISON' not found in any events"}]}),
        ("gate_shipfile_invalid", {"error": "shipfile mentions MARKER_POISON in a bad YAML block"}),
        ("high_risk_change", {"task_class": "feature", "description": "declared MARKER_POISON manually"}),
        (
            "high_risk_change_refused",
            {"task_class": "feature", "description": "declared MARKER_POISON manually"},
        ),
    ],
)
def test_runtime_evidence_self_generated_record_types_are_never_evidence(
    tmp_path, excluded_record_type, payload
):
    """Enumerates the full closed set of the gate's own self-generated record types
    (checkers.py's own allowlist comment names all four) -- each, alone in an otherwise
    empty ledger, must render UNVERIFIED_VACUOUS (nothing GENUINE was ever observed),
    never a false VERIFIED just because the marker string happens to appear in it."""
    _ledger_with_event(tmp_path, payload, record_type=excluded_record_type)
    result = check_runtime_evidence(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "MARKER_POISON"},
        tmp_path,
    )
    assert result.verdict == Verdict.UNVERIFIED_VACUOUS
    assert result.observed is False


def test_runtime_evidence_self_poisoning_reproduced_pre_fix_then_prevented(tmp_path):
    """The exact P38 item-3 sequence, reproduced against real ledger writes: a genuine
    hook event WITHOUT the marker (a real CONTRADICTED result), followed by the
    `gate_evaluation` event `shipgate.gate.orchestrator` would really write recording
    that CONTRADICTED reason -- which, by construction, quotes the marker verbatim. A
    second evaluation must still render CONTRADICTED, not the false VERIFIED the
    unfiltered pre-fix version of this checker produced."""
    db_path = tmp_path / ".shipgate" / "ledger.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    detail = "SHIPGATE_ONLY_FIRES_ONCE_REAL"
    with LedgerWriter(db_path, corpus_root=tmp_path) as writer:
        writer.insert_session(session_id="s1", project_slug="proj", source_dir=".")
        # A real hook fired, but never actually emitted the marker.
        writer.insert_event(
            session_id="s1",
            source_file="<test>",
            source_offset=0,
            transcript_tier="session",
            record_type="posttooluse_hook",
            timestamp="2026-08-15T00:00:00Z",
            raw_payload={"tool_result": "unrelated output, no marker here"},
        )

    condition = {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": detail}

    first = check_runtime_evidence(condition, tmp_path)
    assert first.verdict == Verdict.CONTRADICTED
    # This is the actual self-poisoning mechanism: the checker's own reason string
    # names `detail` verbatim, exactly like `shipgate.gate.orchestrator` would record it.
    assert detail in first.reason

    with LedgerWriter(db_path, corpus_root=tmp_path) as writer:
        writer.insert_event(
            session_id="s1",
            source_file="<gate-orchestrator>",
            source_offset=0,
            transcript_tier="session",
            record_type="gate_evaluation",
            timestamp="2026-08-15T00:01:00Z",
            raw_payload={"conditions": [{"id": "re1", "reason": first.reason}]},
        )

    second = check_runtime_evidence(condition, tmp_path)
    assert second.verdict == Verdict.CONTRADICTED, (
        "self-poisoning regression: a second evaluation found its own prior failure's "
        "recorded reason and rendered a false VERIFIED"
    )


# --- dispatch ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "condition,expected_verdict",
    [
        ({"id": "d1", "type": "file_exists", "path": "SESSION_LOG.md"}, Verdict.CONTRADICTED),
        ({"id": "d2", "type": "forbidden_pattern_absent", "pattern": "TODO"}, Verdict.UNVERIFIED_VACUOUS),
    ],
)
def test_run_checker_dispatches_to_the_right_checker(tmp_path, condition, expected_verdict):
    result = run_checker(condition, tmp_path)
    assert result.verdict == expected_verdict


def test_run_checker_on_runtime_evidence_dispatches_correctly(tmp_path):
    _ledger_with_event(tmp_path, {"note": "MARKER_X"})
    result = run_checker(
        {"id": "re1", "type": "runtime_evidence", "method": "log_marker", "detail": "MARKER_X"}, tmp_path
    )
    assert result.verdict == Verdict.VERIFIED


def test_run_checker_on_inventory_complete_gives_a_specific_redirect_not_a_generic_error():
    """inventory_complete IS implemented (task 2.3) -- the error must say so and say
    where to call it instead, not lump it in with the truly-unimplemented types."""
    with pytest.raises(ValueError, match="check_inventory_complete directly"):
        run_checker(
            {"id": "d3", "type": "inventory_complete", "grep_pattern": "x", "required_dispositions": []}, Path(".")
        )


def test_run_checker_on_a_truly_not_yet_implemented_type_raises_a_specific_error_not_a_silent_pass():
    """emission_traced has no assigned task yet -- must fail loudly and specifically,
    never fall through to something that looks like a pass."""
    with pytest.raises(ValueError, match="emission_traced"):
        run_checker({"id": "d4", "type": "emission_traced", "marker": "x"}, Path("."))


def test_default_checker_timeout_leaves_headroom_inside_the_verified_hook_budget():
    """Founder review finding: the 600s Claude Code default hook timeout this constant
    is sized against was previously cited with no recorded source. Now verified
    (https://code.claude.com/docs/en/hooks.md): a command hook with no `timeout` field
    defaults to 600 seconds. A single tests_pass pytest check spends this constant
    TWICE (see reporters/pytest_reporter.py's run_pytest), so its worst case is 2x this
    constant. Regression guard, not a design test -- fails loudly if a future edit
    raises the constant back toward consuming the hook's own budget."""
    worst_case_for_one_tests_pass_check = 2 * DEFAULT_CHECKER_TIMEOUT_SECONDS
    verified_hook_default_timeout_seconds = 600
    assert worst_case_for_one_tests_pass_check < verified_hook_default_timeout_seconds / 2, (
        "a single tests_pass check's worst case must leave at least half of the "
        "verified 600s hook budget free for other done_conditions and overhead"
    )
