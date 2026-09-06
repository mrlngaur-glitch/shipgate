"""Deterministic checkers (task 2.2, Phase 2) — `tests_pass`,
`file_exists`, `forbidden_pattern_absent`, `command_succeeds`. Each takes one
`done_conditions` entry (already validated by `shipgate.shipfile`) and a project root,
and returns a `CheckResult`: a verdict, the evidence tier when applicable, a reason, and
whether the checker actually observed something.

**Vacuous-pass detection is built in per checker from day one (task 2.5), not bolted on
after — and stated honestly per checker, including where it structurally doesn't apply,
rather than forced everywhere it doesn't fit:**

- `tests_pass` — **yes**, when the command is recognized as a pytest invocation:
  `reporters.pytest_reporter` reports a real collected-count; zero collected renders
  `unverified-vacuous`, never green. For a non-pytest test command, no collected-count
  machinery exists yet (report §13.1: "reporters/ — pytest first, vitest second") — this
  is a **stated gap**, not a silently skipped one; see `_pytest_extra_args`.
- `forbidden_pattern_absent` — **yes**: zero files scanned (an empty/missing `paths`
  target) is exactly as vacuous as zero tests collected — "found nothing" must not look
  identical to "searched nothing." Counted and checked explicitly.
- `file_exists` — **no vacuous mode exists, and that's a considered answer, not an
  oversight.** Checking whether one path exists is inherently binary; there is no
  "searched nothing" state to distinguish from "confirmed absent" — an existence check
  always observes something.
- `command_succeeds` — **no generic vacuous mode.** An arbitrary shell command has no
  universal "did this actually do real work" signal this checker can assert on without
  inventing an unreliable heuristic (empty stdout ≠ "ran nothing," for instance) — a
  structural limitation, stated here rather than faked with a guess.
- `inventory_complete` (task 2.3) — **yes**: 0 real grep matches under the whole project
  is exactly as vacuous as 0 files scanned or 0 tests collected — nothing to inventory
  means a clean claimed list confirms nothing.
- `runtime_evidence`, `log_marker` method (task 2.4) — **yes**: no ledger at all, or a
  ledger with zero events carrying a payload, means nothing was ever observed to search
  — rendered vacuous, not treated as "marker absent" (which is a real negative result
  from a real, non-empty search — see below).

**Founder review finding, fixed this session: the `tests_pass`
non-pytest fallback used to render `VERIFIED`/`RUNTIME_VERIFIED` on a bare exit code
0 — the exact D1 lie this checker exists to catch, produced by the checker itself. A
command like `npm test` can exit 0 having run zero tests (common in default Jest
configs with no test files matched). An exit code alone does not confirm "tests
actually ran"; for `tests_pass` specifically, that IS the question. The caveat used
to live only in the `reason` string — prose a Ship Report screenshot doesn't
re-render as a verdict. It is now `Verdict.UNVERIFIED` with `observed=False`: the
check ran and could not establish the claim either way. **Not `UNVERIFIED_VACUOUS`,
a deliberate choice**: that verdict is reserved for a *positively confirmed* empty
observation (zero tests collected IS itself a real, counted signal); a bare exit
code from an unrecognized runner confirms nothing about whether tests ran at all,
which is `UNVERIFIED`'s own definition ("no evidence either way") more precisely
than vacuous's "ran and confirmed nothing was there."

**Second founder finding, fixed this session: neither `subprocess.run` call in this
module had a timeout.** A hung test command or build step would block forever —
two of this project's own standing rules — "nothing synchronous in any request path"
and "an infinite enforcement loop is a worse failure than a lie" — violated simultaneously the moment
task 2.6/2.8 wire a checker call into the `Stop` hook's synchronous path. Both
`subprocess.run` calls now pass `timeout=`; a timeout renders `Verdict.UNVERIFIED`
with `observed=False` and a reason naming the timeout — the same verdict as the
non-pytest-fallback fix above, and for the identical reason: a process killed before
it reports anything is "no evidence either way," not a confirmed-empty observation.
`gate_policy` is a **frozen Gate-A block** — no field was added there; the bound is a
plain module constant (`DEFAULT_CHECKER_TIMEOUT_SECONDS`), overridable per call for
callers (and tests) that need a different one, exactly the way `reporters.
pytest_reporter`'s own timeout is handled.

**Third founder finding, fixed this session — the timeout fix above was incomplete.**
`subprocess.run(command, shell=True, timeout=...)` does not reliably bound execution
time on Windows: `shell=True`'s immediate child is `cmd.exe`; killing `cmd.exe` on
timeout does not kill a grandchild `cmd.exe` spawned (Windows has no POSIX-style
process-group signal by default), and `subprocess.run`'s own cleanup path blocks
draining stdout/stderr pipes the still-running grandchild holds open — so the call
doesn't actually return until the grandchild eventually exits on its own. Caught by
this session's own timeout tests running far longer than their stated timeout (a
30-second sleep, given `timeout=1`, took the full 30 seconds — the exact "isolated
test passed, realistic test would have failed" gap the founder has now named three
times running). Fixed with the standard, dependency-free tree-kill pattern
(`_run_shell_command_bounded`, below): the child starts in its own process group
(POSIX `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP`), and a timeout kills
the **whole tree** — `os.killpg` on POSIX, the OS-provided `taskkill /T /F` on Windows
(no new dependency; both ship with their respective OS). Both `shell=True` call sites
(`check_tests_pass`'s non-pytest fallback, `check_command_succeeds`) now go through
this helper instead of a bare `subprocess.run`.

**Fourth founder finding, fixed this session: the 600-second hook-timeout figure the
timeout constant above was sized against (see "Second founder finding") was never
verified against a source — it was an assumed number doing load-bearing work, exactly
the class of claim this project's own accuracy rules exist to catch, and the founder caught it on our own
code rather than trusting the comment. Verified against the official Claude Code hooks
reference (https://code.claude.com/docs/en/hooks.md): a `command` hook with no
`timeout` field defaults to 600 seconds, configurable per-hook via an integer `timeout`
field. The number itself turned out correct, but "assumed and happened to be right" is
not the same claim as "verified" — see `reporters.pytest_reporter`'s docstring for the
full source quote and the headroom math this constant is now sized against (60s per
subprocess call, not 300s: a single `tests_pass` pytest check makes two calls, so its
worst case is 120s — about a fifth of the verified 600s hook budget, not effectively
the whole thing).

**Sixth founder finding — two real defects, found by testing the self-gating proposal
instead of reading it, both fixed here, root cause:**

**Blocker 1 (`command_succeeds`) and Blocker 2 (`tests_pass`'s fallback) share one
root problem: a nonzero exit code was treated as proof the checked thing failed, when
it can just as easily mean the checked thing never ran at all** — the command wasn't
found, the interpreter's own module was missing, the path didn't resolve. Reproduced
live, both directions:

- `check_command_succeeds` against `ruff check .` when `.venv/Scripts` isn't on the
  child's `PATH` (exactly the shape of running the `Stop` hook via its
  `.claude/settings.json` absolute-interpreter command, never through an activated
  shell): `returncode == 1`, stderr `"'ruff' is not recognized as an internal or
  external command..."` — CONTRADICTED, a false accusation against a linter that is,
  in fact, clean.
- `check_tests_pass`'s non-pytest fallback against both real pilot shipfiles' own
  `tests_pass` command, the literal string ``venv\\Scripts\\python.exe -m pytest -q``
  (all of this project's real pilot projects use this shape — the correct, idiomatic
  way to write it on Windows): `_pytest_extra_args` never recognized it as a pytest invocation at
  all, for a narrower, upstream reason (next paragraph) — every one of the pilots'
  `contradicted` verdicts on record was actually `"No module named pytest"` (the
  pilot venvs never had pytest installed), never a real test failure.

**Root cause of the non-recognition, traced to the tokenizer, not the recognizer's own
allowlist:** `_pytest_extra_args` used `shlex.split(command)` — POSIX mode by default,
which treats a backslash as an escape character. Splitting the literal string
``venv\\Scripts\\python.exe -m pytest -q`` that way returns
`['venvScriptspython.exe', '-m', 'pytest', '-q']` — the backslash path is silently
mangled into one unrecognizable token before the `first in ("python", "python3",
"python.exe")` check ever runs. The allowlist itself was never the problem; the
input it received already was garbage. **Fixed at the root**: the leading
interpreter token is now extracted with a dedicated, quote-aware,
`shlex`-independent splitter (`_split_leading_command_token`) that never treats a
backslash as an escape character, handling an unquoted Windows path (the literal
``venv\\Scripts\\python.exe`` shape, the pilots' own), a quoted one with embedded
spaces (``"C:\\Program Files\\...\\python.exe"``), and a plain `pytest`/`python -m
pytest` command identically; only the *remaining* args (after the interpreter token
is peeled off) go through `shlex.split` in its normal POSIX mode, unchanged, so
existing quoted-arg behavior (`-k "test name"`) is not disturbed. Basename matching
(`_PYTHON_BASENAME_RE`) uses a portable manual slash-splitting regex rather than
`pathlib.Path(...).name`, which only understands the forward slash as a separator on
POSIX — this recognizer must behave identically whether it happens to run on the
Windows machine a pilot's shipfile targets or on Linux CI exercising the same test.

**Once a command IS recognized as a pytest invocation, the existing collected-count
machinery already renders the right thing (`UNVERIFIED_VACUOUS`, not `CONTRADICTED`,
on `collected == 0` — see `check_tests_pass` below) regardless of *why* collection
produced nothing** — a missing `pytest` module and an empty test directory both
collect zero, and this checker was never required to (and does not try to)
distinguish the two; `unverified-vacuous` already covers both honestly. The
recognition bug was the whole defect for the pytest-shaped case.

**For every other case — a non-pytest command, or `command_succeeds`, where no
collected-count machinery exists at all — the fix is a new, explicit "could the
command even execute" check (`_command_could_not_execute`) run before a nonzero exit
is allowed to render `CONTRADICTED`.** POSIX shells conventionally return exit code
127 for "command not found" — checked first, cheaply. Windows' `cmd.exe` has no such
dedicated code (a real failing linter and a missing one both come back as plain `1`),
so this also matches a short, explicit, comment-justified allowlist of the actual
wordings each shell/interpreter is known to use (`"is not recognized as an internal
or external command"`, `"command not found"`, `": not found"`, `"no such file or
directory"`, `"no module named"`). **Deliberately conservative in the safe direction,
stated as such, not silently picked:** a false negative here (a real not-found case
this list doesn't happen to match) reproduces today's bug in the same direction it
already existed in — no regression. A false positive (a genuine tool failure whose
own output happens to contain one of these phrases) trades a correct `CONTRADICTED`
for an `UNVERIFIED` — the founder's own explicit instruction for this exact
ambiguity: *"if you cannot tell them apart, the honest verdict is unverified."* Not
`UNVERIFIED_VACUOUS`: that verdict means a real, completed search that positively
confirmed emptiness (see the module docstring above); "the shell never even launched
the program" has no completed search behind it at all — `UNVERIFIED`'s own definition
("no evidence either way") is the precise match, matching this file's own established
convention for a timeout or an unrecognized-runner exit-0 (see above).

**A third, related option the founder offered — resolving project-local tooling
(prepending the project's own `venv`/`.venv`/`Scripts`/`bin` onto `PATH` before
running a `command_succeeds` command) — was considered and not built, stated as a
decision rather than silently skipped:** it would require guessing which of several
possible venv directory names/layouts is the right one with no shipfile field naming
it, a heuristic this project's own "never guess on architecture" rule discourages
adding here; the verdict-semantics fix above achieves the same practical goal (no
more false `CONTRADICTED` against unresolvable tooling) with a smaller, more
certain change. Named as a stated, closed decision, not left as an unresolved stub —
worth reconsidering if a future shipfile field ever names a project's own
interpreter/tool root explicitly.

**Seventh founder finding — the Sixth founder finding's own fix introduced a new
false-GREEN, the dangerous direction, live in pilot data. Root cause traced past
this module entirely, into `reporters.pytest_reporter`:**

The Sixth founder finding widened `_pytest_extra_args`'s recognizer to accept an
explicit interpreter path (`venv\\Scripts\\python.exe -m pytest -q`) — but
`reporters.pytest_reporter.run_pytest` still unconditionally built its argv as
`[sys.executable, "-m", "pytest", ...]`, discarding whatever interpreter the
shipfile actually named. The recognizer now correctly *recognized* the command as
pytest-shaped; the reporter it handed off to then silently ran **this gate's own
Python** instead — never the interpreter the shipfile named, and never the project
whose test result the recognizer had just promised to check. Reproduced live and
verbatim, the founder's own repro: a shipfile command naming a literal, nonexistent
path (`C:\\NO\\SUCH\\PATH\\python.exe -m pytest -q`) rendered `VERIFIED, 1 passed, 1
collected` — the collected test was this gate's OWN test suite, run by this gate's
own interpreter, credited to a path that does not exist on the machine. Live in
a real pilot project's data: its real `tests_pass` command now renders `CONTRADICTED,
0 failed, 43 errored, of 885 collected` — the 885 collected tests are ShipGate's
own dogfooded suite dressed up as the pilot's; 40 of the 43 "errors" are
`ModuleNotFoundError`s for the pilot's own dependencies, absent from ShipGate's venv —
the Sixth founder finding's exact false-accusation shape, reproduced by the
Seventh's own fix, now wearing convincing-looking runtime evidence. The ledger
would have recorded a `gate_evaluation` naming the pilot's own command string against a
result that has nothing to do with it — the append-only evidence record stating
something ran that did not, worse than any single wrong verdict, per the founder's
own explicit instruction.

**Fixed at the root, in the layer that actually owns the substitution decision.**
`reporters.pytest_reporter._pytest_invocation`/`collect_test_count`/`run_pytest` now
accept an explicit `base_argv` — the resolved interpreter/executable prefix to
actually invoke — defaulting to `[sys.executable, "-m", "pytest"]` **only** when
`base_argv` is omitted, which `check_tests_pass` below now only does for a
genuinely bare command (`pytest ...`, `python -m pytest ...` — nothing specific
named; substituting this process's own interpreter there is the founder's own
stated defensible case, since nothing more specific was named to substitute for).
`_recognize_pytest_command` (replacing `_pytest_extra_args`'s narrower return
value — kept as a thin wrapper for existing callers) now also reports whether the
leading token was directory-qualified (`shape.named_interpreter`, the literal
token as the shipfile wrote it, or `None` for a bare name) and which invocation
shape it implies (`pytest_executable`: run it directly; `python_module`: `<it> -m
pytest`). `check_tests_pass` resolves a named interpreter against `project_root`
(the same "shipfile paths are project-relative" convention `file_exists` and
`forbidden_pattern_absent`'s `paths` already use — never this process's own cwd or
`PATH`) **before** ever invoking it: if the resolved path does not exist,
`Verdict.UNVERIFIED` is returned immediately, no subprocess ever runs, and no
misattributed evidence ever reaches a checker result. If it does exist, that exact
resolved path is what actually gets invoked, and `PytestResult.command` (now
always `base_argv`-derived, never a silent `sys.executable`) becomes the literal
argv threaded into the `CheckResult.reason` alongside the shipfile's own command
text — the recorded evidence names both what the shipfile asked for and what
actually ran, never conflating the two into one string the way a bare `command:
{command!r}` used to.

**A residual `OSError` (e.g. permissions, or a path that exists but is not
actually executable) can still surface from the real subprocess call even after
the existence pre-check above** — caught explicitly around `run_pytest` and
rendered `Verdict.UNVERIFIED`, same reasoning as the pre-check: "could not run the
check at all" is definitionally not evidence for or against the tests themselves,
regardless of which of the two moments (before or during the call) the failure to
launch was actually detected in.

Zero Claude-Code-specific imports — core-purity contract, `pyproject.toml`. This module
depends on `reporters.pytest_reporter` (a sibling top-level package, also
harness-agnostic) and `shipgate.verdicts` only.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reporters.pytest_reporter import DEFAULT_PYTEST_TIMEOUT_SECONDS, run_pytest
from shipgate.verdicts import EvidenceTier, Verdict

#: Deliberately the *same* constant as `reporters.pytest_reporter`'s, not just the same
#: value — see that module's docstring for the verified 600s hook-timeout source and
#: the headroom math this number is sized against. Kept as one definition, imported
#: here rather than re-declared, so the two can never silently drift apart the way the
#: 600s figure itself was silently unverified before this session.
DEFAULT_CHECKER_TIMEOUT_SECONDS = DEFAULT_PYTEST_TIMEOUT_SECONDS

def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kills `proc` and everything it spawned, not just `proc` itself. See the module
    docstring's "Third founder finding" for why a bare `proc.kill()` isn't enough when
    `proc` is a shell (`cmd.exe` / `/bin/sh`) wrapping the real command."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass  # best-effort reap; the tree is dead either way, just not yet reaped


def _run_shell_command_bounded(command: str, *, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    """`subprocess.run(command, shell=True, timeout=...)`, but with a timeout that
    actually bounds wall-clock time regardless of platform — see the module docstring.
    Starts the child in its own process group/job so a timeout can kill the whole tree,
    not just the shell wrapping the real command.

    **Fifth founder finding, fixed this session — `text=True`
    with no `encoding=` decodes the child's stdout/stderr using the HOST machine's locale
    (`locale.getpreferredencoding()`), `errors="strict"` by default.** On Windows,
    `communicate()` reads each pipe on a background thread (`Lib/subprocess.py::
    _readerthread`) that does not catch exceptions — a `UnicodeDecodeError` there kills
    the thread silently, `communicate()` returns as if nothing happened, and the affected
    stream comes back `None` instead of a string. A project whose own test output (or a
    conftest's collection-time print) contains a byte sequence the *host's* codepage can't
    decode — nothing to do with what the project intended to print, or in what encoding —
    silently destroys that stream's capture. Fixed by decoding explicitly as UTF-8 (what a
    project's own tooling overwhelmingly actually emits today, the same choice
    `shipgate.cli`'s Finding-4 fix made) with `errors="replace"`, never `"strict"` (would
    still raise, just deterministically instead of only on a mismatched host locale) and
    never `"ignore"` (would silently discard the evidence that something was
    undecodable — an inserted U+FFFD is honest, dropped bytes are not). See
    `shipgate.gate.orchestrator.evaluate_gate`'s docstring for the second, structural half
    of this fix — decoding safely narrows the trigger; it doesn't by itself guarantee a
    checker can never silently escape to `Stop`'s fail-open catch for some other reason."""
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        # The tree is now actually dead, so draining the pipes should return promptly —
        # unlike the bare subprocess.run path this replaces, which could block here
        # for as long as the (still-alive) grandchild kept the pipes open.
        stdout, stderr = proc.communicate(timeout=5)
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


#: Directory names never descended into while scanning for `forbidden_pattern_absent`,
#: `inventory_complete`, and (task 2.7) `shipgate.gate.orchestrator`'s project-wide
#: content fingerprint — tool/VCS noise that is never itself the target of a shipfile's
#: forbidden-pattern check, and scanning it would make every project's own dependency
#: tree part of every grep (slow, and a false-positive risk against generated/vendored
#: code). **Public** (not `_`-prefixed): shared across modules, not private to this one.
IGNORED_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache",
     ".import_linter_cache", ".shipgate", ".mypy_cache"}
)


@dataclass(frozen=True)
class CheckResult:
    """One checker's answer for one `done_conditions` entry. Does not write to the
    ledger — that's the caller's job (a hook, or a future gate-orchestrator module);
    this stays a pure function of (condition, project state) so it's testable without a
    database in every test."""

    condition_id: str
    verdict: Verdict
    evidence_tier: EvidenceTier | None
    reason: str
    observed: bool


# --- tests_pass -----------------------------------------------------------------------


#: Recognized pytest-executable basenames (after stripping any directory prefix) —
#: extended in the founder's own "not just python.exe" direction is unnecessary here:
#: an interpreter is matched by `_PYTHON_BASENAME_RE` below, this set is only for the
#: bare-`pytest`-executable shape.
_PYTEST_EXECUTABLE_BASENAMES = frozenset({"pytest", "pytest.exe", "py.test"})

#: `python`, `python3`, `python3.12`, `python.exe`, `python3.exe`, all case-insensitive
#: (Windows filesystems are case-insensitive; a shipfile author copy-pasting from
#: `where python` or similar may have any casing) — deliberately does NOT match
#: `pythonw.exe` (the windowed/no-console variant nobody uses for `-m pytest`) or
#: bare `py` (the Windows py-launcher takes a different argv shape this module
#: doesn't attempt to parse).
_PYTHON_BASENAME_RE = re.compile(r"^python(?:3(?:\.\d+)?)?(?:\.exe)?$", re.IGNORECASE)


def _split_leading_command_token(command: str) -> tuple[str, str] | None:
    """Splits `command` into `(first_token, remaining_command_text)`. Deliberately
    NOT `shlex.split` for this first token: `shlex`'s default POSIX mode treats `\\`
    as an escape character, which silently mangles an unquoted Windows path —
    `shlex.split(r"venv\\Scripts\\python.exe -m pytest")` returns
    `['venvScriptspython.exe', ...]`, found live against the pilots' own real
    shipfile commands (see module docstring, "Sixth founder finding"). Handles a
    quoted first token with embedded spaces (`"C:\\Program Files\\...\\python.exe"
    -m pytest`) and a plain unquoted one (`pytest -q`, `venv\\Scripts\\python.exe -m
    pytest`) identically. Returns `None` for an empty command or an unterminated
    quote (not a recognizable shape — falls through to the bare-command fallback,
    same as any other unparseable command already did)."""
    stripped = command.strip()
    if not stripped:
        return None
    if stripped[0] in "\"'":
        quote = stripped[0]
        end = stripped.find(quote, 1)
        if end == -1:
            return None
        return stripped[1:end], stripped[end + 1 :].lstrip()
    head, _, rest = stripped.partition(" ")
    return head, rest.lstrip()


@dataclass(frozen=True)
class _PytestCommandShape:
    """What `_recognize_pytest_command` found. `kind`: `"pytest_executable"` (the
    command names `pytest`/`py.test` itself — invoke that path directly) or
    `"python_module"` (`<interpreter> -m pytest` — invoke `<interpreter> -m
    pytest`). `named_interpreter`: the literal leading token exactly as the
    shipfile wrote it, when it was directory-qualified (`venv\\Scripts\\python.exe`,
    `./bin/pytest`, an absolute path...) — `None` when nothing specific was named
    (a bare `pytest`, `python`, `python3`), the one case defensibly substitutable
    with this process's own interpreter (see module docstring, "Seventh founder
    finding"). `extra_args`: the argv tail after the interpreter/executable and any
    `-m pytest`, already `shlex.split` in normal POSIX mode."""

    kind: str
    named_interpreter: str | None
    extra_args: list[str]


def _recognize_pytest_command(command: str) -> _PytestCommandShape | None:
    """Recognizes `command` as a pytest invocation (`pytest ...`, `py.test ...`, or
    any `python`/`python3`/`python3.NN` executable — absolute, relative, quoted,
    `.exe` or not, forward- or back-slash separated — followed by `-m pytest ...`),
    returning the shape `check_tests_pass` needs to actually run the RIGHT
    interpreter (see "Seventh founder finding"), or `None` if the command isn't
    pytest-shaped at all. Only the leading interpreter token is extracted specially
    (see `_split_leading_command_token`); the remaining args still go through
    normal `shlex.split` (POSIX mode), so a quoted trailing arg (`-k "test name"`)
    still tokenizes exactly as before."""
    split = _split_leading_command_token(command)
    if split is None:
        return None
    first_token, rest = split
    first = re.split(r"[\\/]+", first_token)[-1]
    # A directory component was present (a slash was stripped to get `first`) means
    # the shipfile named a SPECIFIC path, not just a bare command name resolved from
    # PATH -- see "Seventh founder finding": this distinction is now load-bearing,
    # not cosmetic.
    named_interpreter = first_token if first_token != first else None

    if first in _PYTEST_EXECUTABLE_BASENAMES:
        try:
            extra_args = shlex.split(rest)
        except ValueError:
            return None
        return _PytestCommandShape("pytest_executable", named_interpreter, extra_args)

    if _PYTHON_BASENAME_RE.match(first):
        try:
            rest_tokens = shlex.split(rest)
        except ValueError:
            return None
        if rest_tokens[:2] == ["-m", "pytest"]:
            return _PytestCommandShape("python_module", named_interpreter, rest_tokens[2:])

    return None


def _pytest_extra_args(command: str) -> list[str] | None:
    """Thin backward-compatible wrapper over `_recognize_pytest_command`, returning
    just the extra pytest args — the original, narrower question this function used
    to answer alone, before "Seventh founder finding" made the recognized
    interpreter/executable path itself load-bearing too. Kept for existing callers
    that only need to know whether a command is pytest-shaped and what its trailing
    args are."""
    shape = _recognize_pytest_command(command)
    return shape.extra_args if shape is not None else None


def _resolve_named_interpreter(named_interpreter: str, project_root: Path) -> Path:
    """A named interpreter/executable path from a shipfile command is resolved
    relative to the PROJECT's own root, never this process's own cwd or `PATH` —
    the same "a shipfile path means relative to this project" convention
    `check_file_exists` and `check_forbidden_pattern_absent`'s `paths` already use.
    Does not check existence itself (a pure path join) — `check_tests_pass` checks
    `.is_file()` on the result before ever invoking it."""
    path = Path(named_interpreter)
    return path if path.is_absolute() else project_root / path


#: Textual signals that the shell/interpreter could not find or launch the target
#: program at all — as distinct from launching it and getting a real, failing exit
#: code. See module docstring, "Sixth founder finding": a conservative, explicit
#: allowlist, not a blanket "any nonzero exit" or "the word not found anywhere"
#: heuristic — a miss here just reproduces today's status quo (no regression); a
#: false match trades a CONTRADICTED for an UNVERIFIED, the safe direction per the
#: founder's own instruction ("if you cannot tell them apart, the honest verdict is
#: unverified").
_COMMAND_NOT_FOUND_SIGNALS = (
    "is not recognized as an internal or external command",  # Windows cmd.exe
    "command not found",  # bash / zsh
    ": not found",  # POSIX sh, e.g. "sh: 1: ruff: not found"
    "no such file or directory",  # POSIX exec() failure surfaced by some shells
    "no module named",  # `<interpreter> -m <missing module>` — Blocker 2's own shape
)


def _command_could_not_execute(proc: subprocess.CompletedProcess) -> bool:
    """`True` when the evidence indicates the target program never actually ran —
    the shell/interpreter itself failed to resolve or launch it — as opposed to the
    program running and exiting with a real failure. POSIX shells conventionally use
    exit code 127 for "command not found"; Windows `cmd.exe` has no distinct code for
    this (a real failing tool and a missing one both come back as plain `1`), so this
    also checks `stderr` for the well-known phrasings each shell/interpreter is
    actually known to use — see `_COMMAND_NOT_FOUND_SIGNALS`."""
    if proc.returncode == 127:
        return True
    stderr_lower = (proc.stderr or "").lower()
    return any(signal in stderr_lower for signal in _COMMAND_NOT_FOUND_SIGNALS)


def check_tests_pass(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    condition_id = condition["id"]
    command = condition["command"]
    shape = _recognize_pytest_command(command)

    if shape is not None:
        base_argv: list[str] | None = None
        if shape.named_interpreter is not None:
            # Seventh founder finding: a named interpreter must actually be the one
            # invoked, resolved against the PROJECT root, checked BEFORE any
            # subprocess runs -- a nonexistent path must never fall through to
            # silently running this gate's own interpreter instead.
            resolved = _resolve_named_interpreter(shape.named_interpreter, project_root)
            if not resolved.is_file():
                return CheckResult(
                    condition_id,
                    Verdict.UNVERIFIED,
                    None,
                    reason=(
                        f"shipfile-named interpreter {shape.named_interpreter!r} "
                        f"(resolved: {resolved}) does not exist under {project_root} "
                        f"(shipfile command: {command!r}) — could not run the check at "
                        "all; not a failure of the tests themselves"
                    ),
                    observed=False,
                )
            base_argv = (
                [str(resolved)]
                if shape.kind == "pytest_executable"
                else [str(resolved), "-m", "pytest"]
            )

        try:
            result = run_pytest(
                project_root, pytest_args=shape.extra_args, timeout=timeout, base_argv=base_argv
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                condition_id,
                Verdict.UNVERIFIED,
                None,
                reason=(
                    f"pytest did not finish within {timeout}s under {project_root} "
                    f"(command: {command!r}) — killed before it reported anything; "
                    "no evidence either way, not a pass"
                ),
                observed=False,
            )
        except OSError as exc:
            # Residual: the existence pre-check above passed but the real subprocess
            # call still couldn't launch it (permissions, not actually executable,
            # a race). Same "could not run the check at all" reasoning either way.
            return CheckResult(
                condition_id,
                Verdict.UNVERIFIED,
                None,
                reason=(
                    f"could not execute {base_argv!r} under {project_root} "
                    f"(shipfile command: {command!r}): {exc} — could not run the check "
                    "at all; not a failure of the tests themselves"
                ),
                observed=False,
            )
        if not result.ran_any_tests:
            return CheckResult(
                condition_id,
                Verdict.UNVERIFIED_VACUOUS,
                None,
                reason=(
                    f"pytest collected 0 tests under {project_root} via "
                    f"'{' '.join(result.command)}' (shipfile command: {command!r}) — a "
                    "clean run here confirms nothing was checked, not that everything passed"
                ),
                observed=False,
            )
        if result.all_passed:
            return CheckResult(
                condition_id,
                Verdict.VERIFIED,
                EvidenceTier.RUNTIME_VERIFIED,
                reason=(
                    f"{result.passed} passed, {result.collected} collected via "
                    f"'{' '.join(result.command)}' (shipfile command: {command!r})"
                ),
                observed=True,
            )
        return CheckResult(
            condition_id,
            Verdict.CONTRADICTED,
            None,
            reason=(
                f"{result.failed} failed, {result.errors} errored, of {result.collected} "
                f"collected via '{' '.join(result.command)}' (shipfile command: {command!r})"
            ),
            observed=True,
        )

    # Non-pytest test command: no collected-count reporter exists for it yet (stated
    # gap, see module docstring) — falls back to a bare exit-code check.
    try:
        proc = _run_shell_command_bounded(command, cwd=project_root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command did not finish within {timeout}s (command: {command!r}) — "
                "killed before it reported anything; no evidence either way, not a pass"
            ),
            observed=False,
        )
    if proc.returncode == 0:
        # Founder review finding, fixed: an exit code alone does not confirm "tests
        # actually ran" for a runner this module doesn't have collected-count
        # machinery for — UNVERIFIED, not VERIFIED. See module docstring.
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command exited 0 (command: {command!r}), but this checker has no "
                "collected-count machinery for a non-pytest runner and cannot confirm "
                "any test actually ran — not a pass. See reporters/pytest_reporter.py"
            ),
            observed=False,
        )
    if _command_could_not_execute(proc):
        # Sixth founder finding (Blocker 2's general shape): a nonzero exit from a
        # command that never actually ran — not found, module missing — is "no
        # evidence either way," not a failing test. See module docstring.
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command could not be run (command: {command!r}): "
                f"{proc.stderr[:300]!r} — the shell/interpreter itself failed to "
                "resolve or launch it; no evidence either way about whether tests "
                "pass, not a failure of the tests themselves"
            ),
            observed=False,
        )
    return CheckResult(
        condition_id,
        Verdict.CONTRADICTED,
        None,
        reason=f"command exited {proc.returncode} (command: {command!r})",
        observed=True,
    )


# --- file_exists ------------------------------------------------------------------------


def check_file_exists(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """`timeout` is accepted but unused — no subprocess runs here. Kept in the
    signature so every entry in `CHECKERS` is callable identically by `run_checker`,
    rather than special-casing which checkers take a timeout and which don't."""
    del timeout
    condition_id = condition["id"]
    rel_path = condition["path"]
    full_path = project_root / rel_path
    if full_path.exists():
        return CheckResult(
            condition_id, Verdict.VERIFIED, EvidenceTier.DISK_VERIFIED,
            reason=f"{rel_path} exists", observed=True,
        )
    return CheckResult(
        condition_id, Verdict.CONTRADICTED, None,
        reason=f"{rel_path} does not exist under {project_root}", observed=True,
    )


# --- forbidden_pattern_absent -----------------------------------------------------------


def iter_scanned_files(project_root: Path, paths: list[str]):
    """Public (task 2.7): the same file walk `forbidden_pattern_absent` and
    `inventory_complete` already used privately, now shared with
    `shipgate.gate.orchestrator`'s content-fingerprint computation so there is one
    definition of "which files this project's scan-based checks look at," not two that
    could silently drift apart."""
    for rel in paths:
        target = project_root / rel
        if target.is_file():
            yield target
        elif target.is_dir():
            for candidate in target.rglob("*"):
                if candidate.is_file() and not (IGNORED_DIR_NAMES & set(candidate.relative_to(project_root).parts)):
                    yield candidate
        # a path that doesn't exist yields nothing -- feeds the vacuous count honestly,
        # rather than being treated as an error or silently skipped


def check_forbidden_pattern_absent(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """`timeout` is accepted but unused — a filesystem walk + regex scan has no
    subprocess to bound. Kept in the signature for the same reason as
    `check_file_exists`."""
    del timeout
    condition_id = condition["id"]
    pattern = condition["pattern"]
    paths = condition.get("paths") or ["."]
    regex = re.compile(pattern)

    scanned = 0
    matches: list[str] = []
    for file_path in iter_scanned_files(project_root, paths):
        scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if regex.search(text):
            # .as_posix(), not str(): same cross-platform reasoning as
            # check_inventory_complete's match strings, below — a Ship Report
            # shouldn't render Windows-only backslashes in a path.
            matches.append(file_path.relative_to(project_root).as_posix())

    if scanned == 0:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=f"0 files scanned under {paths} — nothing was searched, so a clean result proves nothing",
            observed=False,
        )
    if matches:
        shown = matches[:5]
        suffix = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
        return CheckResult(
            condition_id,
            Verdict.CONTRADICTED,
            None,
            reason=f"pattern {pattern!r} found in {len(matches)} file(s): {shown}{suffix}",
            observed=True,
        )
    return CheckResult(
        condition_id,
        Verdict.VERIFIED,
        EvidenceTier.DISK_VERIFIED,
        reason=f"pattern {pattern!r} absent across {scanned} scanned file(s) under {paths}",
        observed=True,
    )


# --- inventory_complete (task 2.3) ------------------------------------------------------
#
# report §2.2 (D3): "'Documented' ≠ 'exhaustively enumerated' — four worst incidents were
# fixing 1 call site when a grep would find 5." The shipfile gives a grep pattern and the
# allowed dispositions; the AGENT is the one who is supposed to produce the enumerated
# list with a disposition per hit. That enumeration isn't part of the shipfile (the
# shipfile is written once, before any specific incident) and doesn't exist anywhere else
# this checker has access to yet — no claims-extraction pipeline is wired up this session
# — so `claimed_items` is an explicit parameter here, not something this function derives
# on its own. This is why `check_inventory_complete` is deliberately NOT in `CHECKERS`/
# `run_checker`'s uniform dispatch: its signature is architecturally different from the
# other four, not just unimplemented.


def check_inventory_complete(
    condition: dict[str, Any],
    project_root: Path,
    claimed_items: list[dict[str, str]],
    timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS,
) -> CheckResult:
    """`claimed_items`: the agent's own enumeration, e.g.
    `[{"match": "shipgate/foo.py:12", "disposition": "updated"}, ...]` — each item names
    the specific real match it accounts for (so a real grep hit can be reconciled against
    a specific claimed item, not just compared by count) and a disposition drawn from
    `condition["required_dispositions"]`.

    Fully deterministic (report §2.2 D3): grep really runs, the real match set is
    compared against the claimed set exactly, and any of three failure shapes blocks —
    a real match missing from the claim, a claimed item no real grep found (fabricated
    or stale), or a claimed item with a disposition outside the allowed list."""
    del timeout  # no subprocess here — a plain filesystem walk + regex, like forbidden_pattern_absent
    condition_id = condition["id"]
    pattern = condition["grep_pattern"]
    required_dispositions = set(condition["required_dispositions"])
    regex = re.compile(pattern)

    real_matches: list[str] = []
    for file_path in iter_scanned_files(project_root, ["."]):
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # .as_posix(), not str(rel): a bare WindowsPath str() uses backslashes, which
        # would make a match string built on Windows silently unmatchable against a
        # claimed_items list built (or tested) with forward slashes — the same
        # cross-platform trap shipgate/ledger/paths.py already guards against for
        # source_dir, for the identical reason.
        rel = file_path.relative_to(project_root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                real_matches.append(f"{rel}:{lineno}")

    if not real_matches:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=(
                f"grep pattern {pattern!r} matched 0 lines under {project_root} — there is "
                "nothing to inventory, so a clean result here confirms nothing was searched"
            ),
            observed=False,
        )

    real_match_set = set(real_matches)
    claimed_match_set = {item["match"] for item in claimed_items}

    missing = sorted(real_match_set - claimed_match_set)
    extra = sorted(claimed_match_set - real_match_set)
    bad_dispositions = [
        item
        for item in claimed_items
        if item["match"] in real_match_set and item.get("disposition") not in required_dispositions
    ]

    if missing or extra or bad_dispositions:
        parts = []
        if missing:
            parts.append(f"{len(missing)} real match(es) not in the claimed inventory: {missing[:5]}")
        if extra:
            parts.append(f"{len(extra)} claimed item(s) no real grep found: {extra[:5]}")
        if bad_dispositions:
            shown = [f"{i['match']} -> {i.get('disposition')!r}" for i in bad_dispositions[:5]]
            parts.append(f"{len(bad_dispositions)} claimed item(s) with a disallowed disposition: {shown}")
        return CheckResult(condition_id, Verdict.CONTRADICTED, None, reason="; ".join(parts), observed=True)

    return CheckResult(
        condition_id,
        Verdict.VERIFIED,
        EvidenceTier.DISK_VERIFIED,
        reason=f"{len(real_matches)} real match(es) for {pattern!r}, all claimed with an allowed disposition",
        observed=True,
    )


# --- runtime_evidence, log-marker variant (task 2.4) ------------------------------------
#
# report §2.2 (D1): "'Complete on disk' ≠ 'verified in runtime' — agent ships code, tests
# pass, agent says 'fix is active' — but the running daemon never reloaded it." Grepping
# SOURCE CODE for a marker string would only prove the marker exists in the file, exactly
# the disk-only claim D1 warns against. This checker instead searches this project's own
# ledger (`<project_root>/.shipgate/ledger.db`) — real events a hook actually recorded
# during a real session — for the marker appearing in a REAL, OBSERVED payload. Only the
# `log_marker` method is built this session (task 2.4's stated scope); `endpoint_probe`
# and `db_query` remain schema-only and raise a specific error, not a silent pass.
#
# **Self-poisoning fix (PHASE_PLAN.md P38, item 3 — a real, found-live false-GREEN
# mechanism, the more dangerous error class for a product whose whole pitch is never
# rendering one).** A prior version of this checker searched EVERY event's
# `raw_payload_redacted` with no exclusion for the gate's OWN past output. But
# `record_type='gate_evaluation'` events (written by `shipgate.gate.orchestrator`) embed
# every dispatched condition's own `reason` string in their payload — and this checker's
# own CONTRADICTED reason, below, literally quotes the `detail` marker it was searching
# for ("marker {detail!r} not found..."). The first real failure's reason therefore
# becomes, itself, a future match: a second evaluation searching for the identical
# marker finds its own prior failure's recorded text and renders a false `VERIFIED` —
# nothing to do with whether the marker was ever actually, independently observed.
#
# Fixed with an explicit ALLOWLIST of record types that represent a genuine, externally
# observed event (a real tool call, a real tool result, a real Stop invocation) —
# deliberately not a denylist of the gate's own self-generated types. A denylist would
# silently admit any NEW self-generated record_type this project adds later (a future
# `gate_*` bookkeeping event) unless someone remembered to add it to the denylist too —
# exactly the kind of silent gap this project exists to refuse elsewhere (the same
# reasoning behind the `<synthetic>`-model price-table exclusion being an explicit
# allowlist, not an exclude-list). `gate_evaluation` and `gate_shipfile_invalid` are both
# the gate DESCRIBING its own outcome; `high_risk_change`/`high_risk_change_refused` are
# a self-declared blast-radius record, not evidence that anything actually ran. None of
# the four are in this allowlist. A new genuine hook type must be added here explicitly
# before this checker will ever search it.
_GENUINE_OBSERVED_RECORD_TYPES = ("pretooluse_hook", "posttooluse_hook", "stop_hook")


def check_runtime_evidence(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    del timeout  # no subprocess here — a local sqlite read
    condition_id = condition["id"]
    method = condition["method"]
    detail = condition["detail"]

    if method != "log_marker":
        raise ValueError(
            f"runtime_evidence method {method!r} has no checker implemented yet "
            f"(condition id: {condition_id!r}) — only 'log_marker' is built (task 2.4); "
            "'endpoint_probe' and 'db_query' are schema-only so far"
        )

    db_path = project_root / ".shipgate" / "ledger.db"
    if not db_path.exists():
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=f"no ledger at {db_path} — no hook has ever run in this project; nothing to search",
            observed=False,
        )

    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in _GENUINE_OBSERVED_RECORD_TYPES)
        rows = conn.execute(
            "SELECT raw_payload_redacted FROM events "
            f"WHERE raw_payload_redacted IS NOT NULL AND record_type IN ({placeholders})",
            _GENUINE_OBSERVED_RECORD_TYPES,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED_VACUOUS,
            None,
            reason=(
                f"ledger at {db_path} exists but has 0 genuinely-observed events "
                f"(record_type in {_GENUINE_OBSERVED_RECORD_TYPES}) with a payload — "
                "nothing was ever observed"
            ),
            observed=False,
        )

    matches = sum(1 for (payload,) in rows if payload and detail in payload)
    if matches > 0:
        return CheckResult(
            condition_id,
            Verdict.VERIFIED,
            EvidenceTier.RUNTIME_VERIFIED,
            reason=f"marker {detail!r} found in {matches} real recorded event(s) in {db_path}",
            observed=True,
        )

    # A real, non-empty search of real, genuinely-observed events found zero matches --
    # a confirmed negative, not an absence of evidence. Source code can contain this
    # exact marker string and this still renders CONTRADICTED if it never actually
    # ran (the D1 lie) -- see test_runtime_evidence_marker_in_source_but_never_run_...
    #
    # This reason string itself still names `detail` verbatim -- deliberately left
    # readable rather than obscured -- but it lands in a `record_type='gate_evaluation'`
    # event, which is EXCLUDED from `_GENUINE_OBSERVED_RECORD_TYPES` above, so it can
    # never poison a later evaluation the way it did before the P38 fix.
    return CheckResult(
        condition_id,
        Verdict.CONTRADICTED,
        None,
        reason=(
            f"marker {detail!r} not found in any of {len(rows)} genuinely-observed "
            f"event(s) in {db_path} — code may exist on disk but was never observed running"
        ),
        observed=True,
    )


# --- command_succeeds ---------------------------------------------------------------------


def check_command_succeeds(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """`command` runs via `shell=True`. The shipfile is owner-authored by convention,
    but by construction it is writable by the gated agent itself — `shipfile.yaml` is a
    file in the project tree like any other, not a channel with its own permission
    check. This is a known, accepted trust boundary, not an oversight: an agent that
    edits a `command_succeeds` condition can have this checker run arbitrary shell at
    gate-evaluation time, outside Claude Code's own per-command permission prompt.

    `shell=True` is a deliberate, accepted design choice here, not an oversight, for
    two stated reasons: (1) a shell-syntax command (pipes, `&&`) is the whole point of
    letting an author write an arbitrary command here, and (2) the gated agent already
    has a Bash tool and gains no capability it didn't already have in the normal case —
    this checker adds a second path to the same place, not a new destination. What this
    docstring no longer claims, because the record doesn't support it: that shipfile
    content is a different, more-trusted class of input than untrusted remote input. It
    isn't. It is accepted exposure, consciously named, not verified-safe input."""
    condition_id = condition["id"]
    command = condition["command"]
    try:
        proc = _run_shell_command_bounded(command, cwd=project_root, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command did not finish within {timeout}s (command: {command!r}) — "
                "killed before it reported anything; no evidence either way, not a pass"
            ),
            observed=False,
        )
    if proc.returncode == 0:
        return CheckResult(
            condition_id, Verdict.VERIFIED, EvidenceTier.RUNTIME_VERIFIED,
            reason=f"command exited 0 (command: {command!r})", observed=True,
        )
    if _command_could_not_execute(proc):
        # Sixth founder finding (Blocker 1): "'ruff' is not recognized" is "I could
        # not run your linter," not "your linter failed" — see module docstring.
        return CheckResult(
            condition_id,
            Verdict.UNVERIFIED,
            None,
            reason=(
                f"command could not be run (command: {command!r}): "
                f"{proc.stderr[:300]!r} — the shell/interpreter itself failed to "
                "resolve or launch it, not a failure of the underlying tool"
            ),
            observed=False,
        )
    return CheckResult(
        condition_id, Verdict.CONTRADICTED, None,
        reason=f"command exited {proc.returncode} (command: {command!r}): {proc.stderr[:300]}",
        observed=True,
    )


# --- dispatch ---------------------------------------------------------------------------

#: `inventory_complete` is deliberately excluded — its signature takes an extra
#: `claimed_items` argument the other five don't need (see that function's docstring),
#: so it can't be dispatched uniformly through `run_checker`. Call it directly.
CHECKERS: dict[str, Callable[[dict[str, Any], Path, float], CheckResult]] = {
    "tests_pass": check_tests_pass,
    "file_exists": check_file_exists,
    "forbidden_pattern_absent": check_forbidden_pattern_absent,
    "command_succeeds": check_command_succeeds,
    "runtime_evidence": check_runtime_evidence,
}


def run_checker(
    condition: dict[str, Any], project_root: Path, timeout: float = DEFAULT_CHECKER_TIMEOUT_SECONDS
) -> CheckResult:
    """Dispatches on `condition["type"]`. Raises `ValueError` for a condition type with
    no checker implemented yet (`emission_traced` — no task assigned yet — and the `ears`
    representation, which is never dispatched to a checker at all), and for
    `inventory_complete`, which is implemented but not dispatchable here — see
    `check_inventory_complete`'s docstring and call it directly."""
    cond_type = condition.get("type")
    if cond_type == "inventory_complete":
        raise ValueError(
            "inventory_complete is implemented but cannot be dispatched through "
            "run_checker — it needs a claimed_items argument the other condition types "
            "don't take. Call shipgate.gate.checkers.check_inventory_complete directly."
        )
    checker = CHECKERS.get(cond_type)
    if checker is None:
        raise ValueError(
            f"no deterministic checker implemented yet for condition type {cond_type!r} "
            f"(condition id: {condition.get('id')!r}) — implemented so far: "
            f"{sorted({*CHECKERS, 'inventory_complete'})}"
        )
    return checker(condition, project_root, timeout)


__all__ = [
    "CHECKERS",
    "DEFAULT_CHECKER_TIMEOUT_SECONDS",
    "IGNORED_DIR_NAMES",
    "CheckResult",
    "check_command_succeeds",
    "check_file_exists",
    "check_forbidden_pattern_absent",
    "check_inventory_complete",
    "check_runtime_evidence",
    "check_tests_pass",
    "iter_scanned_files",
    "run_checker",
]
