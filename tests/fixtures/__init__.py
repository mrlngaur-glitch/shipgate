"""Fixture modules invoked as real subprocesses by tests that need a genuine external
process (not a mock) shaped a specific way — e.g. `noop_hook.py`, used by
`tests/unit/test_latency_bench.py` to prove the latency benchmark's vacuous-pass guard
actually fires against a hook-shaped process that exits 0 without doing real work."""
