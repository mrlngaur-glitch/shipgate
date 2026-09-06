#!/bin/bash
# Demo scenario for ShipGate GIF recording
# This script demonstrates ShipGate's core workflow in a reproducible way

set -e

echo "=== ShipGate Demo Scenario ==="
echo ""
echo "Step 1: Create a fresh demo project"
DEMO_DIR="shipgate_demo_$(date +%s)"
mkdir -p "$DEMO_DIR"
cd "$DEMO_DIR"

echo "Step 2: Create a simple test file"
cat > test_example.py << 'EOF'
def add(a, b):
    return a + b

def test_add():
    assert add(2, 3) == 5
EOF

echo "Step 3: Initialize ShipGate"
shipgate init --project-dir . --intent-summary "Demo project showing ShipGate workflow" --test-command "pytest -q"

echo ""
echo "Step 4: Show generated files"
echo "--- shipfile.yaml ---"
cat shipfile.yaml
echo ""
echo "--- .claude/settings.json ---"
cat .claude/settings.json

echo ""
echo "Step 5: Run the test suite"
pytest -q

echo ""
echo "Step 6: Simulate a hook firing (Stop hook with minimal JSON)"
echo '{"session_id":"demo-session-001","cwd":"'"$(pwd)"'","hook_event_name":"Stop","transcript_path":"","permission_mode":"tool","stop_hook_active":false}' | python -m shipgate.hooks.stop

echo ""
echo "Step 7: Check gate status"
shipgate status --project-dir .

echo ""
echo "Step 8: View full Ship Report"
shipgate report --project-dir .

echo ""
echo "=== Demo Complete ==="
echo "Demo directory: $DEMO_DIR"
echo "To clean up: cd .. && rm -rf $DEMO_DIR"
