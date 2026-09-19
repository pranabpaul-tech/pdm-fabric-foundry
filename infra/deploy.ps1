<#
.SYNOPSIS
  Orchestrates the three Bicep waves for PdM Copilot, handing off to the
  Python provisioning scripts in between (they create the Fabric workspace items
  that Wave 2 and Wave 3 depend on).

.DESCRIPTION
  This script does NOT run everything unattended — Wave 2 and Wave 3 both need
  IDs that only exist after a Python step has run and, for the Operations Agent,
  after a human has authored it once in the Fabric portal. It stops and tells you
  what to do between waves.

.PARAMETER Wave
  1, 2, or 3. Defaults to 1.

.PARAMETER SubscriptionId
  Azure subscription to deploy into. Uses the current az CLI context if omitted.

.EXAMPLE
  ./deploy.ps1 -Wave 1
  # ... run src/pdmops/setup/01_workspace.py through 05_network_policy.py ...
  ./deploy.ps1 -Wave 2
  # ... run foundry/deploy_hosted_agent.py ...
  ./deploy.ps1 -Wave 3
#>
param(
  [ValidateSet(1, 2, 3)]
  [int]$Wave = 1,

  [string]$SubscriptionId,

  [string]$ResourceGroupName = 'rg-pdm-fabric-foundry',

  [string]$StateFile = "$PSScriptRoot/../state.json"
)

$ErrorActionPreference = 'Stop'

if ($SubscriptionId) {
  az account set --subscription $SubscriptionId
}

function Read-State {
  if (Test-Path $StateFile) {
    return Get-Content $StateFile -Raw | ConvertFrom-Json -AsHashtable
  }
  return @{}
}

function Write-State($state) {
  $state | ConvertTo-Json -Depth 10 | Set-Content $StateFile
}

switch ($Wave) {
  1 {
    Write-Host "== Wave 1: network, F8 capacity, Key Vault, monitoring, jumpbox, Foundry ==" -ForegroundColor Cyan
    Write-Host "Make sure infra/main.bicepparam has real values (fabricAdminMembers, operatorPrincipalId, BYO Search/Storage/Cosmos IDs) before continuing." -ForegroundColor Yellow

    $outputs = az deployment sub create `
      --location (Select-String -Path "$PSScriptRoot/main.bicepparam" -Pattern "param location = '(.+)'").Matches.Groups[1].Value `
      --template-file "$PSScriptRoot/main.bicep" `
      --parameters "$PSScriptRoot/main.bicepparam" `
      --query properties.outputs | ConvertFrom-Json -AsHashtable

    $state = Read-State
    $state['wave1'] = $outputs
    Write-State $state

    Write-Host "`nWave 1 deployed. Outputs written to $StateFile under .wave1" -ForegroundColor Green
    Write-Host "Next: run foundry-vendored/createCapHost.sh if the Foundry capability host didn't come up automatically (see infra/README.md)." -ForegroundColor Yellow
    Write-Host "Then: az container exec into the jumpbox and run setup/01_workspace.py through setup/04_eventstream.py, then setup/05_network_policy.py --confirm." -ForegroundColor Yellow
  }
  2 {
    Write-Host "== Wave 2: workspace-level Fabric private link ==" -ForegroundColor Cyan
    $state = Read-State
    if (-not $state.ContainsKey('workspace') -or -not $state.workspace.workspaceId) {
      throw "state.json has no .workspace.workspaceId yet — run setup/01_workspace.py first."
    }
    if (-not $state.ContainsKey('wave1')) {
      throw "state.json has no .wave1 outputs — run -Wave 1 first."
    }

    $outputs = az deployment group create `
      --resource-group $ResourceGroupName `
      --template-file "$PSScriptRoot/wave2-fabric-privatelink.bicep" `
      --parameters workspaceId=$($state.workspace.workspaceId.value) `
      --parameters location=$($state.wave1.location.value) `
      --parameters vnetId=$($state.wave1.vnetId.value) `
      --parameters peSubnetId=$($state.wave1.peSubnetId.value) `
      --query properties.outputs | ConvertFrom-Json -AsHashtable

    $state['wave2'] = $outputs
    Write-State $state
    Write-Host "`nWave 2 deployed. Verify from the jumpbox: nslookup <workspaceId-no-dashes>.z<xy>.w.api.fabric.microsoft.com must resolve to a private IP (can take up to 24h after capacity creation)." -ForegroundColor Yellow
    Write-Host "Then run setup/05_network_policy.py --confirm if you haven't already, then foundry/deploy_hosted_agent.py." -ForegroundColor Yellow
  }
  3 {
    Write-Host "== Wave 3: bot service + Teams channel ==" -ForegroundColor Cyan
    $state = Read-State
    if (-not $state.ContainsKey('foundry_agent') -or -not $state.foundry_agent.clientId) {
      throw "state.json has no .foundry_agent.clientId yet — run foundry/deploy_hosted_agent.py first."
    }

    $outputs = az deployment group create `
      --resource-group $ResourceGroupName `
      --template-file "$PSScriptRoot/wave3-bot.bicep" `
      --parameters msaAppId=$($state.foundry_agent.clientId) `
      --parameters activityEndpoint=$($state.foundry_agent.activityEndpoint) `
      --query properties.outputs | ConvertFrom-Json -AsHashtable

    $state['wave3'] = $outputs
    Write-State $state
    Write-Host "`nWave 3 deployed. Now run foundry/publish_teams.py to PATCH the agent's protocol config and publish at Shared scope, then validate/e2e_flow.py." -ForegroundColor Green
  }
}
