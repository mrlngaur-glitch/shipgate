"""Task 3.6 — proves the latency benchmark harness's own vacuous-pass guard (binding
constraint 2, `PHASE_PLAN.md` 3.6) actually fires, against a real subprocess, before the
harness is ever trusted to report a real number. See `benchmarks/latency_bench.py`'s
module docstring for the full reasoning this file exercises.
"""

import sqlite3
import sys

import pytest

from benchmarks.latency_bench import (
    MIN_SAMPLES,
    VacuousBenchmarkError,
    _percentile,
    measure_hook,
)

PY = sys.executable
_NOOP_MODULE = "tests.fixtures.noop_hook"
_REAL_MODULE = "shipgate.hooks.pretooluse"


def test_percentile_nearest_rank():
    samples = [float(i) for i in range(1, 101)]  # 1..100
    assert _percentile(samples, 50) == 50.0 or _percentile(samples, 50) == 51.0
    assert _percentile(samples, 99) in (99.0, 100.0)
    assert _percentile(samples, 0) == 1.0
    assert _percentile(samples, 100) == 100.0


def test_percentile_over_zero_samples_refuses():
    with pytest.raises(VacuousBenchmarkError):
        _percentile([], 99)


def test_measure_hook_below_sample_floor_refuses_without_spawning_anything(tmp_path):
    """Constraint 2's sample-count half: a `p99` under `MIN_SAMPLES` is not a percentile
    estimate. Checked *before* any subprocess is spawned — proven here by asserting no
    ledger was even created, not just that the call raised."""
    with pytest.raises(VacuousBenchmarkError, match="minimum is"):
        measure_hook(PY, _REAL_MODULE, tmp_path, n_samples=1, min_samples=MIN_SAMPLES)
    assert not (tmp_path / ".shipgate").exists()


def test_measure_hook_guard_fires_on_a_real_process_that_exercised_nothing(tmp_path):
    """The core of constraint 2: a real subprocess that exits 0 (`tests/fixtures/
    noop_hook.py` — a genuine process, not a mock) but never touches the ledger must
    make this harness refuse to report a result, not report a suspiciously fast p99."""
    with pytest.raises(VacuousBenchmarkError, match="not actually exercised"):
        measure_hook(PY, _NOOP_MODULE, tmp_path, n_samples=3, min_samples=1)


def test_measure_hook_passes_guard_against_the_real_hook(tmp_path):
    """Negative control for the two tests above: the guard must NOT fire on the real
    hook path doing real work — proves the guard discriminates real exercise from a
    no-op, rather than just always refusing."""
    timing = measure_hook(PY, _REAL_MODULE, tmp_path, n_samples=5, min_samples=1)

    assert timing.n == 5
    assert all(s > 0 for s in timing.samples_ms)

    conn = sqlite3.connect(tmp_path / ".shipgate" / "ledger.db")
    try:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM events WHERE record_type = 'pretooluse_hook'"
        ).fetchone()
    finally:
        conn.close()
    assert count == 5
