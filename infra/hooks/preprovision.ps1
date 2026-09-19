# Runs right before `azd provision` deploys anything. Prompts for the four
# names the user is most likely to want to change per-deployment — resource
# group, Foundry account base name, Fabric capacity name, and the hosted
# agent's technical name — instead of silently reusing whatever's baked into
# infra/main.bicepparam / .env. Press Enter at any prompt to keep the current
# default.
#
# The first three also get threaded into infra/main.bicepparam via
# readEnvironmentVariable(), so azd env set here is what actually reaches the
# Bicep deployment. All four are additionally written to .env so the Python
# setup scripts (common/config.py's Settings, PDMOPS_-prefixed) agree with
# what was just deployed — state.json is still the real source of truth once
# resources exist, but a fresh clone needs these before anything's been created.

$ErrorActionPreference = 'Stop'
$repoRoot = Resolve-Path "$PSScriptRoot/../.."

Push-Location $repoRoot
try {
    function Prompt-WithDefault([string]$Message, [string]$Default) {
        # Read-Host throws in a non-interactive host (CI, automation) rather
        # than just returning empty — fall back to the default silently
        # there instead of failing the whole `azd provision`.
        try {
            $value = Read-Host "$Message [$Default]"
        } catch {
            return $Default
        }
        if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
        return $value
    }

    function Set-EnvValue([string]$Key, [string]$Value) {
        $envFile = ".env"
        if (Test-Path $envFile) {
            $lines = Get-Content $envFile | Where-Object { $_ -notmatch "^$Key=" }
        } else {
            $lines = @()
        }
        $lines + "$Key=$Value" | Set-Content $envFile
    }

    function Get-EnvValue([string]$Key, [string]$Default) {
        $envFile = ".env"
        if (Test-Path $envFile) {
            $existing = Get-Content $envFile | Where-Object { $_ -match "^$Key=" }
            if ($existing) { return ($existing -split '=', 2)[1] }
        }
        return $Default
    }

    function Get-AzdValue([string]$Key) {
        # azd env get-value prints its "key not found" error to STDOUT and,
        # worse, still exits 0 — the only way to tell success from failure
        # is the text itself. It can ALSO print an unrelated "Update
        # available: ..." notice to stdout ahead of the real output.
        # Confirmed live: both land in stdout, not stderr, so `2>$null`
        # alone doesn't help — strip known noise lines, then treat what's
        # left as the value (empty/ERROR = not found).
        $out = azd env get-value $Key 2>$null |
            Where-Object { $_ -notmatch '^(Update available:|To update, run|ERROR:|Suggestion:|Run .azd env|\s*$)' }
        return ($out -join "`n")
    }

    Write-Host "`n== Deployment names (press Enter to keep the default) ==" -ForegroundColor Cyan

    $currentRg = Get-AzdValue "AZURE_RESOURCE_GROUP_NAME"
    if ([string]::IsNullOrWhiteSpace($currentRg)) { $currentRg = Get-EnvValue "PDMOPS_RESOURCE_GROUP" "rg-pdm-fabric-foundry" }
    $rg = Prompt-WithDefault "Resource group name" $currentRg
    azd env set AZURE_RESOURCE_GROUP_NAME $rg | Out-Null
    Set-EnvValue "PDMOPS_RESOURCE_GROUP" $rg

    $currentFoundry = Get-AzdValue "FOUNDRY_AI_SERVICES_BASE_NAME"
    if ([string]::IsNullOrWhiteSpace($currentFoundry)) { $currentFoundry = "pdmopsai" }
    $foundry = Prompt-WithDefault "Foundry account base name (a short suffix gets appended)" $currentFoundry
    azd env set FOUNDRY_AI_SERVICES_BASE_NAME $foundry | Out-Null

    $currentFabric = Get-AzdValue "FABRIC_CAPACITY_NAME"
    if ([string]::IsNullOrWhiteSpace($currentFabric)) { $currentFabric = Get-EnvValue "PDMOPS_FABRIC_CAPACITY_NAME" "pdmopsf8" }
    $fabric = Prompt-WithDefault "Fabric capacity name" $currentFabric
    azd env set FABRIC_CAPACITY_NAME $fabric | Out-Null
    Set-EnvValue "PDMOPS_FABRIC_CAPACITY_NAME" $fabric

    $currentAgentName = Get-EnvValue "PDMOPS_FOUNDRY_AGENT_NAME" "pdm-orchestrator"
    $agentName = Prompt-WithDefault "Foundry hosted agent name (technical identifier, no spaces)" $currentAgentName
    Set-EnvValue "PDMOPS_FOUNDRY_AGENT_NAME" $agentName

    # Fabric capacity admin + operator identity (defaults to the signed-in user)
    $currentUpn = Get-AzdValue "FABRIC_ADMIN_UPN"
    if ([string]::IsNullOrWhiteSpace($currentUpn)) { $currentUpn = (az ad signed-in-user show --query userPrincipalName -o tsv 2>$null) }
    $upn = Prompt-WithDefault "Fabric capacity admin UPN" $currentUpn
    azd env set FABRIC_ADMIN_UPN $upn | Out-Null
    $oid = Get-AzdValue "OPERATOR_OBJECT_ID"
    if ([string]::IsNullOrWhiteSpace($oid)) { $oid = (az ad signed-in-user show --query id -o tsv 2>$null) }
    azd env set OPERATOR_OBJECT_ID $oid | Out-Null

    # BYO Search / Storage / Cosmos come from Wave 0 (infra/wave0-byo-resources.bicep) if it has run.
    if ([string]::IsNullOrWhiteSpace((Get-AzdValue "AI_SEARCH_RESOURCE_ID"))) {
        $pairs = @{ "AI_SEARCH_RESOURCE_ID" = "aiSearchResourceId"; "AZURE_STORAGE_RESOURCE_ID" = "azureStorageAccountResourceId"; "AZURE_COSMOS_RESOURCE_ID" = "azureCosmosDBAccountResourceId" }
        foreach ($key in $pairs.Keys) {
            $val = (az deployment group show -g $rg -n wave0-byo-deployment --query "properties.outputs.$($pairs[$key]).value" -o tsv 2>$null)
            if (-not [string]::IsNullOrWhiteSpace($val)) { azd env set $key $val | Out-Null }
        }
    }
    if ([string]::IsNullOrWhiteSpace((Get-AzdValue "AI_SEARCH_RESOURCE_ID"))) {
        Write-Warning "No BYO Search/Storage/Cosmos IDs in the azd env and no wave0-byo-deployment found in '$rg'."
        Write-Warning "Run: az group create -n $rg -l <region>; az deployment group create -g $rg -n wave0-byo-deployment -f infra/wave0-byo-resources.bicep"
        Write-Warning "(or set AI_SEARCH_RESOURCE_ID / AZURE_STORAGE_RESOURCE_ID / AZURE_COSMOS_RESOURCE_ID with azd env set), then re-run azd provision."
    }

    Write-Host "`nUsing: resource group '$rg', Foundry base name '$foundry', Fabric capacity '$fabric', hosted agent '$agentName'." -ForegroundColor Green
}
finally {
    Pop-Location
}
