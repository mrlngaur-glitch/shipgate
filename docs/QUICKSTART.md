# ShipGate Quickstart

This is the fastest path from zero to a real `GATE: GREEN` — no mock data, no hand-waving.

## 1. Install

From a fresh clone of this repo:

```bash
python3.12 -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install --require-hashes -r requirements.lock
pip install -e . --no-deps
```

For the Windows variant the commands are the same except for the `source` line:

```powershell
. .venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.lock
pip install -e . --no-deps
```

## 2. Create a demo project

```bash
mkdir /tmp/sg_demo && cd /tmp/sg_demo
```

Write a tiny passing test:

```python
# tests/test_example.py
def add(a, b):
    return a + b

def test_add():
    assert add(2, 3) == 5
```

## 3. Initialize ShipGate

```bash
shipgate init --project-dir . --intent-summary "Demo project" --test-command "pytest -q"
```

This writes:

- `shipfile.yaml` — your machine-checkable done-conditions.
- `CLAUDE.md` — the session contract.
- `.claude/settings.json` — Claude Code hook wiring.

## 4. Run the test suite

```bash
pytest -q
```

## 5. Simulate the `Stop` hook

```bash
python -m shipgate.hooks.stop << 'EOF'
{
    "session_id": "demo-001",
    "cwd": "$(pwd)",
    "hook_event_name": "Stop",
    "transcript_path": "",
    "permission_mode": "tool",
    "stop_hook_active": false
}
EOF
```

On Windows, run the equivalent from `demo_scenario.ps1` or feed the JSON through `python -m shipgate.hooks.stop`.

## 6. Check the gate

```bash
shipgate status --project-dir .
```

You should see:

```text
[verified] tests-pass: 1 passed, 1 collected (command: 'pytest -q')

GATE: GREEN
```

## 7. View the full Ship Report

```bash
shipgate report --project-dir .
```

The report shows:

- One claim per `done_condition` with `verified`, `runtime-verified`, and the pytest evidence.
- A blast-radius count.
- Token counts (currently zero; price tables do not exist).
- A ledger receipt and a `--verify: VERIFIED` line proving the hash chain is intact.

## 8. Tamper demo (optional)

`shipgate report --verify` recomputes every row hash in the append-only ledger. You can demonstrate it catching a real out-of-band edit:

```bash
python - << 'PY'
import sqlite3
conn = sqlite3.connect('.shipgate/ledger.db')
conn.execute('DROP TRIGGER verdicts_no_update')
conn.execute("UPDATE verdicts SET reason = 'TAMPERED' WHERE verdict_id = 1")
conn.commit()
conn.close()
PY
shipgate report --project-dir .
```

This should end with `--verify: TAMPERED`.

## 9. Scripted version

For a fully automated run, use the provided scenarios:

- `demo_scenario.ps1` (Windows / PowerShell)
- `demo_scenario.sh` (Linux / macOS / WSL)

## What to do next

- Edit `shipfile.yaml` to add your own `done_conditions`.
- Wire the hooks into Claude Code via `.claude/settings.json` (already generated).
- Read `SECURITY.md` for the project's threat model and evidence discipline.
- See `docs/shipfile_worked_example.yaml` for a complete worked example of every block.
