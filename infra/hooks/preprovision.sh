#!/usr/bin/env bash
# Posix equivalent of preprovision.ps1 — see that file for what this does and why.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

prompt_with_default() {
  local message="$1" default="$2" value
  # read exits non-zero on EOF (no tty — CI, automation); with `set -e` that
  # would kill the whole script, so tolerate it and fall back to the default.
  read -r -p "$message [$default]: " value || true
  echo "${value:-$default}"
}

set_env_value() {
  local key="$1" value="$2"
  if [ -f .env ]; then
    grep -v "^$key=" .env > .env.tmp || true
    mv .env.tmp .env
  fi
  echo "$key=$value" >> .env
}

get_env_value() {
  local key="$1" default="$2"
  if [ -f .env ] && grep -q "^$key=" .env; then
    grep "^$key=" .env | cut -d= -f2-
  else
    echo "$default"
  fi
}

# azd env get-value prints its "key not found" error to STDOUT and, worse,
# still exits 0 — the only way to tell success from failure is the text
# itself. It can ALSO print an unrelated "Update available: ..." notice to
# stdout ahead of the real output. Confirmed live: both of these land in
# stdout, not stderr, so `2>/dev/null` alone doesn't help — strip known noise
# lines first, then treat what's left as the value (empty/ERROR = not found).
get_azd_value() {
  local key="$1" val
  val="$(azd env get-value "$key" 2>/dev/null | grep -v -E '^(Update available:|To update, run|ERROR:|Suggestion:|Run .azd env|$)')" || true
  echo "$val"
}

echo ""
echo "== Deployment names (press Enter to keep the default) =="

current_rg="$(get_azd_value AZURE_RESOURCE_GROUP_NAME)"
current_rg="${current_rg:-$(get_env_value PDMOPS_RESOURCE_GROUP rg-pdm-fabric-foundry)}"
rg="$(prompt_with_default "Resource group name" "$current_rg")"
azd env set AZURE_RESOURCE_GROUP_NAME "$rg" >/dev/null
set_env_value "PDMOPS_RESOURCE_GROUP" "$rg"

current_foundry="$(get_azd_value FOUNDRY_AI_SERVICES_BASE_NAME)"
current_foundry="${current_foundry:-pdmopsai}"
foundry="$(prompt_with_default "Foundry account base name (a short suffix gets appended)" "$current_foundry")"
azd env set FOUNDRY_AI_SERVICES_BASE_NAME "$foundry" >/dev/null

current_fabric="$(get_azd_value FABRIC_CAPACITY_NAME)"
current_fabric="${current_fabric:-$(get_env_value PDMOPS_FABRIC_CAPACITY_NAME pdmopsf8)}"
fabric="$(prompt_with_default "Fabric capacity name" "$current_fabric")"
azd env set FABRIC_CAPACITY_NAME "$fabric" >/dev/null
set_env_value "PDMOPS_FABRIC_CAPACITY_NAME" "$fabric"

current_agent_name="$(get_env_value PDMOPS_FOUNDRY_AGENT_NAME pdm-orchestrator)"
agent_name="$(prompt_with_default "Foundry hosted agent name (technical identifier, no spaces)" "$current_agent_name")"
set_env_value "PDMOPS_FOUNDRY_AGENT_NAME" "$agent_name"

# Fabric capacity admin + operator identity (defaults to the signed-in user)
current_upn="$(get_azd_value FABRIC_ADMIN_UPN)"
current_upn="${current_upn:-$(az ad signed-in-user show --query userPrincipalName -o tsv 2>/dev/null || true)}"
upn="$(prompt_with_default "Fabric capacity admin UPN" "${current_upn:-}")"
azd env set FABRIC_ADMIN_UPN "$upn" >/dev/null
oid="$(get_azd_value OPERATOR_OBJECT_ID)"
oid="${oid:-$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)}"
azd env set OPERATOR_OBJECT_ID "$oid" >/dev/null

# BYO Search / Storage / Cosmos come from Wave 0 (infra/wave0-byo-resources.bicep) if it has run.
if [ -z "$(get_azd_value AI_SEARCH_RESOURCE_ID)" ]; then
  for pair in "AI_SEARCH_RESOURCE_ID:aiSearchResourceId" "AZURE_STORAGE_RESOURCE_ID:azureStorageAccountResourceId" "AZURE_COSMOS_RESOURCE_ID:azureCosmosDBAccountResourceId"; do
    key="${pair%%:*}"; out="${pair##*:}"
    val="$(az deployment group show -g "$rg" -n wave0-byo-deployment --query "properties.outputs.${out}.value" -o tsv 2>/dev/null || true)"
    if [ -n "$val" ]; then azd env set "$key" "$val" >/dev/null; fi
  done
fi
if [ -z "$(get_azd_value AI_SEARCH_RESOURCE_ID)" ]; then
  echo "WARNING: no BYO Search/Storage/Cosmos IDs in the azd env and no wave0-byo-deployment found in '$rg'." >&2
  echo "         Run: az group create -n $rg -l <region> && az deployment group create -g $rg -n wave0-byo-deployment -f infra/wave0-byo-resources.bicep" >&2
  echo "         (or set AI_SEARCH_RESOURCE_ID / AZURE_STORAGE_RESOURCE_ID / AZURE_COSMOS_RESOURCE_ID with azd env set), then re-run azd provision." >&2
fi

echo ""
echo "Using: resource group '$rg', Foundry base name '$foundry', Fabric capacity '$fabric', hosted agent '$agent_name'."
