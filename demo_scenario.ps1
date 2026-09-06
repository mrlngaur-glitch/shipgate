# Demo scenario for ShipGate GIF recording (PowerShell version)
# This script demonstrates ShipGate's core workflow in a reproducible way

$ErrorActionPreference = "Stop"

Write-Host "=== ShipGate Demo Scenario ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "Step 1: Create a fresh demo project" -ForegroundColor Yellow
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$demoDir = "shipgate_demo_$timestamp"
New-Item -ItemType Directory -Path $demoDir | Out-Null
Set-Location $demoDir

Write-Host "Step 2: Create a simple test file" -ForegroundColor Yellow
@"
def add(a, b):
    return a + b

def test_add():
    assert add(2, 3) == 5
"@ | Out-File -FilePath test_example.py -Encoding utf8

Write-Host "Step 3: Initialize ShipGate" -ForegroundColor Yellow
shipgate init --project-dir . --intent-summary "Demo project showing ShipGate workflow" --test-command "pytest -q"

Write-Host ""
Write-Host "Step 4: Show generated files" -ForegroundColor Yellow
Write-Host "--- shipfile.yaml ---" -ForegroundColor Gray
Get-Content shipfile.yaml
Write-Host ""
Write-Host "--- .claude/settings.json ---" -ForegroundColor Gray
Get-Content .claude\settings.json

Write-Host ""
Write-Host "Step 5: Run the test suite" -ForegroundColor Yellow
pytest -q

Write-Host ""
Write-Host "Step 6: Simulate a hook firing (Stop hook with minimal JSON)" -ForegroundColor Yellow
$cwd = Get-Location
$json = @{
    session_id = "demo-session-001"
    cwd = $cwd.Path
    hook_event_name = "Stop"
    transcript_path = ""
    permission_mode = "tool"
    stop_hook_active = $false
} | ConvertTo-Json
$json | python -m shipgate.hooks.stop

Write-Host ""
Write-Host "Step 7: Check gate status" -ForegroundColor Yellow
shipgate status --project-dir .

Write-Host ""
Write-Host "Step 8: View full Ship Report" -ForegroundColor Yellow
shipgate report --project-dir .

Write-Host ""
Write-Host "=== Demo Complete ===" -ForegroundColor Green
Write-Host "Demo directory: $demoDir"
Write-Host "To clean up: cd ..; Remove-Item -Recurse -Force $demoDir"
