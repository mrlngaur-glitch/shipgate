# Demo scenario for ShipGate GIF recording (PowerShell version)
# This script demonstrates ShipGate's core workflow in a reproducible way

$ErrorActionPreference = "Stop"

# Use this clone's own .venv when it exists. README's Windows install creates `.venv` but
# never activates it, and every command below (`shipgate`, `pytest`, `python`) is bare,
# so without this a stranger following README literally got "'shipgate' is not
# recognized" at Step 3 -- or worse, a different Python than the one ShipGate was
# installed into. Found by the public re-sync's stranger re-run, 2026-09-22.
$venvScripts = Join-Path $PSScriptRoot ".venv\Scripts"
if (Test-Path (Join-Path $venvScripts "python.exe")) {
    $env:Path = "$venvScripts;$env:Path"
}

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
# Windows PowerShell 5.1 prepends a UTF-8 BOM when piping to a native program whenever the
# console runs UTF-8 (code page 65001, e.g. Windows' "UTF-8 for worldwide language support"),
# even from a -NoProfile shell -- and the Stop hook deliberately rejects undecodable input
# rather than guess (it skips, never blocks). Claude Code itself sends no BOM; this is only
# the demo's own pipe. Both settings are needed (either alone still sent the BOM, reproduced
# 2026-09-22); the console's own setting is restored straight after.
$savedInputEncoding = [Console]::InputEncoding
$savedOutputEncoding = $OutputEncoding
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
try {
    [Console]::InputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
    $json | python -m shipgate.hooks.stop
} finally {
    [Console]::InputEncoding = $savedInputEncoding
    $OutputEncoding = $savedOutputEncoding
}

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
