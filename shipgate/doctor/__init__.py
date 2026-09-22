"""`shipgate doctor` (task 3.2) — shipfile staleness detection, plus (Session 043)
hook wiring diagnostics.
See `check.py`'s module docstring for exactly what "stale" means and which condition
types are checked, and `wiring.py`'s module docstring for the hook wiring check."""

from .check import DoctorReport, StaleReference, run_doctor
from .wiring import HookWiringIssue, WiringReport, check_hook_wiring

__all__ = [
    "DoctorReport",
    "HookWiringIssue",
    "StaleReference",
    "WiringReport",
    "check_hook_wiring",
    "run_doctor",
]
