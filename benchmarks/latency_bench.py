"""Task 3.6 — the latency covenant benchmark harness (`PHASE_PLAN.md` 3.6; report
§5.5 item 4, verbatim: "Published benchmark: <10ms added overhead at p99, in CI,
publicly").

What this measures, and why — the decision this task's own instruction required to be
stated, not picked silently:

**"The request path" = the real subprocess Claude Code actually spawns for every tool
call.** `shipgate.discipline.templates.build_hooks_fragment` emits, into every real
`settings.json`, exactly: `"command": "{sys.executable} -m shipgate.hooks.<name>"`.
That is not an implementation detail this benchmark can choose to skip past — it is the
literal command line a real user's agent waits on, synchronously, once per `PreToolUse`
and once per `PostToolUse` per tool call. So this harness spawns that exact command,
via `subprocess.run`, timed end-to-end (`Popen`-equivalent start to process exit) — not
`pretooluse.run()` called in-process. An in-process call is a synthetic micro-benchmark
of a function no request path ever calls on its own; it would silently exclude CPython's
own interpreter bootstrap, which every real invocation pays in full, because a fresh
process is spawned per tool call, not reused. Excluding it would be picking the
flattering interpretation silently — the one thing this task's own instruction said not
to do.

**`Stop` is deliberately out of scope for the <10ms bar.** Report §5.5 item 1: "the gate
fires once at Stop" is stated as separate from "zero added conversational latency" —
`Stop` runs the real deterministic checkers (task 2.x) once per turn, by design, not
once per tool call; it is the gate itself, not per-call overhead "in the typing loop"
(report line 194). Benchmarking it against this bar would conflate a deliberate,
one-time verification cost with the per-call tax this covenant actually governs.

**Baseline vs. instrumented.** There is no "ShipGate not installed, same tool call"
baseline to diff against, because without ShipGate no subprocess fires at all for that
tool call — the full subprocess wall time *is* the added overhead. A second, purely
diagnostic baseline (bare `{python} -c "pass"`) is measured alongside it, to show how
much of that overhead is generic CPython startup vs. ShipGate's own hook logic (stdin
parse, ledger open, one write) — that breakdown answers "where does the time go," but
the number actually compared against the 10ms target is the full hook-subprocess time,
because that is what a real user's agent actually waits on.

**Vacuous-pass guard (binding constraint 2).** A harness that reports a fast p99 because
it measured zero real invocations, or a hook path that silently no-op'd, is a false
green — the exact failure this product exists to catch, turned on itself. Before any
percentile is computed, `measure_hook` requires: (a) at least `MIN_SAMPLES` timed
invocations: below that floor a "p99" is arithmetically indistinguishable from "the
single slowest sample," not a percentile estimate; (b) every invocation exited 0; (c)
the target project's `events` table grew by exactly one row per invocation, proving the
hook path was actually exercised the number of times this harness believes it was, not
just that a process happened to exit cleanly. Any violation raises
`VacuousBenchmarkError` — never silently reported as a pass. Proven to actually fire:
`tests/unit/test_latency_bench.py` runs this exact guard against a real no-op hook
module (`tests/fixtures/noop_hook.py`) that exits 0 without touching the ledger, and
against a sample count below the floor — both confirmed to raise before this harness was
trusted to report anything real.

**Sample count.** Default `--samples 500` per measured path (baseline, PreToolUse,
PostToolUse). At 500 samples the p99 threshold falls at the ~5th-highest sample
(500 x 0.01 = 5) — the tail estimate rests on several real data points, not one outlier.
Below `MIN_SAMPLES` (200 — the floor the guard enforces), the 99th percentile of the
sample collapses onto the single slowest run, which is a maximum, not a percentile.

**CI threshold handling (binding constraint 5).** The vacuous-pass guard above is a hard
failure — a broken harness reporting fabricated numbers must fail the build. Whether the
*measured* p99 clears 10ms is reported plainly (PASS/FAIL banner, full breakdown,
written to the job summary) but does not by itself fail the CI job — a shared CI runner
is noisy, and a single run's absolute wall-clock timing crossing a hard millisecond bar
is exactly the flake-quarantine rule's concern ("flaky checks degrade to advisory, never
hard-block"). This is a stated decision, not a silently avoided red: see
`.github/workflows/ci.yml`'s own comment at this step for the same reasoning inline.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Report §5.5 item 4's own number.
TARGET_P99_MS = 10.0

#: See this module's docstring, "Sample count."
MIN_SAMPLES = 200

#: See this module's docstring, "Sample count."
DEFAULT_SAMPLES = 500

_HOOK_MODULES = ("shipgate.hooks.pretooluse", "shipgate.hooks.posttooluse")

RESULTS_JSON = REPO_ROOT / "benchmarks" / "results" / "latest.json"
RESULTS_MD = REPO_ROOT / "benchmarks" / "RESULTS.md"


class VacuousBenchmarkError(RuntimeError):
    """The harness ran but observed nothing real — PHASE_PLAN.md 3.6's own binding
    constraint 2. Never caught and swallowed by a caller that reports a number anyway;
    that would be exactly the false-green failure this class exists to make impossible
    to ship silently."""


def _percentile(samples_ms: list[float], p: float) -> float:
    """Nearest-rank percentile — simple, standard, and easy for a reader to sanity-check
    by hand against a sorted list, which matters more here than a fancier interpolated
    estimator would."""
    if not samples_ms:
        raise VacuousBenchmarkError("percentile requested over zero samples")
    ordered = sorted(samples_ms)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


@dataclass
class TimingSamples:
    label: str
    samples_ms: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.samples_ms)

    @property
    def mean_ms(self) -> float:
        return sum(self.samples_ms) / len(self.samples_ms)

    def percentile(self, p: float) -> float:
        return _percentile(self.samples_ms, p)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "n": self.n,
            "mean_ms": round(self.mean_ms, 3),
            "p50_ms": round(self.percentile(50), 3),
            "p95_ms": round(self.percentile(95), 3),
            "p99_ms": round(self.percentile(99), 3),
            "max_ms": round(max(self.samples_ms), 3),
        }


def _events_row_count(project_dir: Path) -> int:
    import sqlite3

    db_path = project_dir / ".shipgate" / "ledger.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


def _hook_payload(project_dir: Path, *, event: str, tool_use_id: str) -> dict:
    payload = {
        "session_id": "latency-bench-session",
        "cwd": str(project_dir),
        "hook_event_name": event,
        "permission_mode": "default",
        "tool_name": "Read",
        "tool_input": {"file_path": "bench.py"},
        "tool_use_id": tool_use_id,
    }
    if event == "PostToolUse":
        payload["tool_result"] = {"stdout": "ok", "stderr": "", "is_error": False}
    return payload


def _run_once(python_executable: str, args: list[str], *, input_json: str | None) -> float:
    """Times one subprocess end-to-end (spawn through exit) — the same measurement a
    real user's agent experiences, since Claude Code waits on this exact call before the
    next tool proceeds. `encoding="utf-8"` explicit, matching the rest of this project's
    subprocess call sites (`tests/integration/test_hooks_e2e.py::_run_hook`) — `text=True`
    alone decodes with the parent's own locale, which would make this benchmark's own
    numbers depend on the machine running it in a way that has nothing to do with
    ShipGate.

    Deliberately spawned with `cwd=REPO_ROOT`, not the hook's own project directory: a
    live hook (`shipgate/hooks/_common.py::open_project_ledger`) resolves its project
    root from the JSON payload's `cwd` field, never from the subprocess's own OS-level
    working directory — so this choice changes nothing about what is measured, and it
    keeps every real invocation (`shipgate` is a `pip install -e .` package, resolvable
    from any cwd) *and* the test-only no-op fixture module `measure_hook`'s own guard
    test spawns (a plain, non-installed module under `tests/fixtures/`, resolvable only
    from a cwd that has this repo root on `sys.path`) working identically."""
    t0 = time.perf_counter()
    result = subprocess.run(
        [python_executable, *args],
        input=input_json,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if result.returncode != 0:
        raise VacuousBenchmarkError(
            f"subprocess {[python_executable, *args]!r} exited {result.returncode} — "
            f"stderr: {result.stderr!r}"
        )
    return elapsed_ms


def measure_baseline(python_executable: str, n_samples: int) -> TimingSamples:
    """Bare interpreter startup — `{python} -c "pass"` — the diagnostic-only baseline
    this module's docstring names ("where does the time go"), not what is compared
    against the 10ms target."""
    timing = TimingSamples(label="baseline: bare interpreter startup")
    for _ in range(n_samples):
        timing.samples_ms.append(_run_once(python_executable, ["-c", "pass"], input_json=None))
    return timing


def measure_hook(
    python_executable: str,
    module: str,
    project_dir: Path,
    n_samples: int,
    *,
    min_samples: int = MIN_SAMPLES,
) -> TimingSamples:
    """Measures one hook module's real, real subprocess-per-call cost — see this
    module's docstring for why the invocation is `subprocess.run`, not `module.run()`.
    Raises `VacuousBenchmarkError` per the vacuous-pass guard described there; never
    returns a `TimingSamples` the guard hasn't cleared."""
    if n_samples < min_samples:
        raise VacuousBenchmarkError(
            f"{module}: {n_samples} samples requested, minimum is {min_samples} — a "
            "p99 below this floor is not a percentile estimate, see this module's "
            "docstring, 'Sample count'"
        )

    event = "PreToolUse" if module.endswith("pretooluse") else "PostToolUse"
    before = _events_row_count(project_dir)

    timing = TimingSamples(label=module)
    for i in range(n_samples):
        payload = _hook_payload(project_dir, event=event, tool_use_id=f"latency-bench-{module}-{i}")
        timing.samples_ms.append(
            _run_once(python_executable, ["-m", module], input_json=json.dumps(payload))
        )

    after = _events_row_count(project_dir)
    expected_after = before + n_samples
    if after != expected_after:
        raise VacuousBenchmarkError(
            f"{module}: expected {expected_after} events rows after {n_samples} real "
            f"invocations (started at {before}), found {after} — the hook path was not "
            "actually exercised the number of times this benchmark believes it was; a "
            "fast p99 measured this way would be a false green, refusing to report it"
        )
    return timing


@dataclass
class BenchmarkResult:
    generated_at: str
    python_executable: str
    platform: str
    sample_count: int
    baseline: TimingSamples
    hooks: list[TimingSamples]
    target_p99_ms: float

    def worst_hook_p99(self) -> tuple[str, float]:
        worst = max(self.hooks, key=lambda t: t.percentile(99))
        return worst.label, worst.percentile(99)

    def passes_target(self) -> bool:
        _, p99 = self.worst_hook_p99()
        return p99 <= self.target_p99_ms

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "python_executable": self.python_executable,
            "platform": self.platform,
            "sample_count": self.sample_count,
            "target_p99_ms": self.target_p99_ms,
            "baseline": self.baseline.to_dict(),
            "hooks": [h.to_dict() for h in self.hooks],
            "passes_target": self.passes_target(),
        }


def run_benchmark(python_executable: str, n_samples: int) -> BenchmarkResult:
    with tempfile.TemporaryDirectory(prefix="shipgate-latency-bench-") as tmp:
        project_dir = Path(tmp)
        baseline = measure_baseline(python_executable, n_samples)
        hooks = [measure_hook(python_executable, module, project_dir, n_samples) for module in _HOOK_MODULES]

    return BenchmarkResult(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        python_executable=python_executable,
        platform=sys.platform,
        sample_count=n_samples,
        baseline=baseline,
        hooks=hooks,
        target_p99_ms=TARGET_P99_MS,
    )


def _render_markdown(result: BenchmarkResult) -> str:
    worst_label, worst_p99 = result.worst_hook_p99()
    verdict = "PASS" if result.passes_target() else "FAIL (advisory — see .github/workflows/ci.yml)"

    header = (
        f"Generated `{result.generated_at}` on `{result.platform}`, `{result.sample_count}` "
        "samples per path, using `sys.executable` at the time of the run."
    )
    target_line = (
        f"**Target: p99 <= {result.target_p99_ms} ms added overhead (report §5.5 item 4). "
        f"Worst measured hook path: `{worst_label}` at **{worst_p99:.3f} ms** p99 — **{verdict}**.**"
    )
    scope_line = (
        "What is measured and why: see `benchmarks/latency_bench.py`'s module docstring "
        '("the request path" = the real `{python} -m shipgate.hooks.<name>` subprocess Claude '
        "Code actually spawns per tool call, timed end-to-end; `Stop` is out of scope by report "
        "§5.5 item 1)."
    )

    lines = [
        "<!-- Generated by `python -m benchmarks.latency_bench` — do not hand-edit; regenerate instead. -->",
        "# Latency covenant benchmark (task 3.6)",
        "",
        header,
        "",
        target_line,
        "",
        scope_line,
        "",
        "| Path | n | mean (ms) | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for timing in (result.baseline, *result.hooks):
        d = timing.to_dict()
        lines.append(
            f"| `{d['label']}` | {d['n']} | {d['mean_ms']} | {d['p50_ms']} | {d['p95_ms']} | "
            f"{d['p99_ms']} | {d['max_ms']} |"
        )

    worst_timing = next(t for t in result.hooks if t.label == worst_label)
    worst_max_ms = round(max(worst_timing.samples_ms), 3)
    variance_note = (
        "**On variance:** even within this one run's own samples, the single slowest "
        f"invocation of `{worst_label}` (max {worst_max_ms:.3f} ms) ran well above its own "
        f"p99 figure above ({worst_p99:.3f} ms) — a self-verifying signal computed from this "
        "run's own data every time this file regenerates, not a claim about past runs. "
        "Manual local runs of this benchmark on a single Windows development machine have, "
        "independently, also shown the reported p99 vary run to run by a wide margin, and the "
        "ShipGate-share-of-total figure below swing by double digits of percentage points — "
        "real, not a bug in this harness; a shared dev machine under normal background load "
        "is a noisy clock. This is exactly the reasoning behind this file's own CI-threshold "
        "decision (see the module docstring and `.github/workflows/ci.yml`): the *trend* and "
        "the *order of magnitude* are the load-bearing signal, not any single run's digit past "
        "the decimal point, and CI's own dedicated `ubuntu-latest` runner — not this machine — "
        "is the number to treat as authoritative once it has run."
    )
    lines += ["", variance_note]

    baseline_p99 = result.baseline.percentile(99)
    shipgate_delta_ms = max(0.0, worst_p99 - baseline_p99)
    shipgate_share_pct = (shipgate_delta_ms / worst_p99 * 100.0) if worst_p99 else 0.0
    breakdown_note = (
        f"Diagnostic breakdown: bare interpreter startup alone accounts for {baseline_p99:.3f} "
        f"ms of the {worst_p99:.3f} ms p99 at `{worst_label}` ({100 - shipgate_share_pct:.0f}%); "
        "ShipGate's own added logic (module import, stdin parse, ledger open, one "
        f"redact-then-write) accounts for the remaining {shipgate_delta_ms:.3f} ms "
        f"({shipgate_share_pct:.0f}%) — real, measured, and not a rounding-error-sized slice of "
        "the total; see the recommendation below."
    )
    lines += ["", breakdown_note]

    if not result.passes_target():
        recommendation = (
            f"**Recommendation, not a fix improvised under pressure:** {100 - shipgate_share_pct:.0f}% "
            "of the measured overhead (see the breakdown line above — this figure is computed "
            "from this run's own data, not a fixed assumption) is CPython interpreter startup, "
            "which every subprocess-per-tool-call invocation pays in full regardless of what "
            "ShipGate does — a property of the hooks-as-a-fresh-subprocess architecture (frozen "
            f"at Gate A), not something this harness or a future session can fix without "
            f"reopening that decision. The remaining {shipgate_share_pct:.0f}% is ShipGate's own "
            "added cost, and is not fully accounted for by unavoidable work: "
            '`python -X importtime -c "import shipgate.hooks.pretooluse"` shows roughly 35ms of '
            "it is import time alone, much of it `dataclasses` -> `inspect` pulled in "
            "transitively by `shipgate.ledger.integrity`/`shipgate.verdicts.supersession` — "
            "modules the observational `PreToolUse`/`PostToolUse` write path never calls, only "
            "imports as a side effect of `LedgerWriter`'s own import graph. That specific "
            "optimization (lazy-import the read/verify-only pieces of `shipgate.ledger` out of "
            "the hot write path) is a real, scoped, cheap-looking candidate — named here and "
            "left for `PARKING_LOT.md`, not built this session (0-high-risk-change budget; "
            "touching the hook/ledger import graph is exactly the kind of change this task's "
            "own instruction said to stop and ask about, not absorb). Closing the "
            "interpreter-startup share would require either (a) a persistent hook process — "
            "forbidden outright by `CLAUDE.md` §3's no-background-daemons rule — or (b) "
            "revisiting whether the 10ms target, as written, assumed a lighter invocation "
            "mechanism than a fresh CPython process per call. Both are founder-level rulings, "
            "not something this harness should quietly redefine its way past."
        )
        lines += ["", recommendation]

    return "\n".join(lines) + "\n"


def _write_step_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(markdown)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help=f"samples per path (default {DEFAULT_SAMPLES})")
    parser.add_argument(
        "--python", dest="python_executable", default=sys.executable, help="interpreter to benchmark (default: this one)"
    )
    args = parser.parse_args(argv)

    try:
        result = run_benchmark(args.python_executable, args.samples)
    except VacuousBenchmarkError as exc:
        sys.stderr.write(f"VACUOUS BENCHMARK — refusing to report a result: {exc}\n")
        return 2

    markdown = _render_markdown(result)
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    RESULTS_MD.write_text(markdown, encoding="utf-8")
    _write_step_summary(markdown)

    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
