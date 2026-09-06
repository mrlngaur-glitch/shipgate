"""Task 3.6 — the latency covenant benchmark harness. See `latency_bench.py`.

Not part of the installed `shipgate` package (`pyproject.toml`'s
`[tool.setuptools.packages.find]` only includes `shipgate*`/`reporters*`) — this runs
from a repo checkout (`python -m benchmarks.latency_bench`), the same way `tests/` is
never installed but is always run from the checkout that owns it.
"""
