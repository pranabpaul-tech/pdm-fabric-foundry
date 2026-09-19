# Runs right after `azd provision` finishes Wave 1 (infra/main.bicep). Creates
# the Fabric workspace items that only exist as API calls, not ARM resources:
# workspace, Eventhouse, KQL schema, Eventstream. Everything after this point
# needs a step only a human can do (Fabric admin portal settings, authoring
# the Operations Agent) — see the README's "Manual steps" section for those.

$ErrorActionPreference = 'Stop'
$repoRoot = Resolve-Path "$PSScriptRoot/../.."

Push-Location $repoRoot
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating Python venv..." -ForegroundColor Cyan
        python -m venv .venv
    }
    & ".venv/Scripts/pip.exe" install -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): & `".venv/Scripts/pip.exe`" install -q -r requirements.txt" }

    $env:PYTHONPATH = "src"
    $env:PDMOPS_FORCE_CLI_CREDENTIAL = "1"

    Write-Host "`n== Recording Wave 1's Bicep outputs into state.json ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" scripts/capture_wave1_state.py
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): & `".venv/Scripts/python.exe`" scripts/capture_wave1_state.py" }

    Write-Host "`n== Creating the Fabric workspace and assigning it to the capacity ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/pdmops/setup/01_workspace.py
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): & `".venv/Scripts/python.exe`" src/pdmops/setup/01_workspace.py" }

    Write-Host "`n== Creating the Eventhouse + KQL database ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/pdmops/setup/02_eventhouse.py
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): & `".venv/Scripts/python.exe`" src/pdmops/setup/02_eventhouse.py" }

    Write-Host "`n== Applying the PdM Eventhouse schema (tables, functions, update policy, views) ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/pdmops/setup/03_kql_schema.py
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): & `".venv/Scripts/python.exe`" src/pdmops/setup/03_kql_schema.py" }

    Write-Host "`n== Creating the downtime Eventstream (skipped until captured from the portal) ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/pdmops/setup/04_eventstream.py --apply --skip-if-placeholder
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): & `".venv/Scripts/python.exe`" src/pdmops/setup/04_eventstream.py --ap" }

    Write-Host "`n== Creating the Operations Agent from the checked-in definition ==" -ForegroundColor Cyan
    & ".venv/Scripts/python.exe" src/pdmops/setup/06_ops_agent.py --apply
    if ($LASTEXITCODE -ne 0) { throw "Step failed (exit $LASTEXITCODE): & `".venv/Scripts/python.exe`" src/pdmops/setup/06_ops_agent.py --appl" }

    Write-Host "`nWave 1 + the whole Fabric phase (workspace, Eventhouse, Eventstream, Operations Agent) are live." -ForegroundColor Green
    Write-Host "See README.md 'Manual steps' for what's left:" -ForegroundColor Green
    Write-Host "  1. Enable two Fabric admin portal tenant settings, then run setup/05_network_policy.py --confirm" -ForegroundColor Yellow
    Write-Host "  2. Deploy infra/wave2-fabric-privatelink.bicep" -ForegroundColor Yellow
    Write-Host "  3. Run foundry/deploy_hosted_agent.py (temporarily makes the Foundry account public, deploys, leaves it public)" -ForegroundColor Yellow
    Write-Host "  4. Deploy infra/wave3-bot.bicep, then run foundry/publish_teams.py (locks the Foundry account back to private once publishing succeeds)" -ForegroundColor Yellow
    Write-Host "  5. In the Fabric portal, click Generate Playbook then Start on the Operations Agent" -ForegroundColor Yellow
}
finally {
    Pop-Location
}
