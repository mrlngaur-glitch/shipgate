"""A hook-shaped process that does nothing real: drains stdin (so it behaves like a
well-formed subprocess a parent can safely write to and wait on) and exits 0 without
touching any ledger. Used by `tests/unit/test_latency_bench.py` to prove
`benchmarks.latency_bench`'s vacuous-pass guard actually fires against a process that
"ran successfully" (exit 0) but exercised nothing — the exact false-green shape the
guard exists to catch, reproduced with a real subprocess, not a mock.
"""

from __future__ import annotations

import sys


def main() -> int:
    sys.stdin.read()
    return 0


if __name__ == "__main__":
    sys.exit(main())
