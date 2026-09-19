// PdM Copilot — Wave 1
// Network, F8 Fabric capacity, Key Vault, monitoring, a container-based
// jumpbox, and the VNet-injected Foundry account/project.
//
// Deliberately NOT included here (see infra/README.md):
//   - Fabric workspace, Eventhouse, Eventstream, Operations Agent — these are
//     Fabric workspace items, not ARM resources. Run src/pdmops/setup/*.py
//     after this deployment finishes.
//   - Workspace-level Fabric private link (infra/wave2-fabric-privatelink.bicep)
//     — needs a workspace ID that only exists after setup/01_workspace.py runs.
//   - The bot service + Teams channel (infra/wave3-bot.bicep) — needs the
//     Foundry agent's own client ID, which only exists after
//     foundry/deploy_hosted_agent.py runs.
//   - The Foundry capability host — run foundry-vendored/createCapHost.sh
//     after this deployment.
//
// Tenant prerequisites this can't do for you (Fabric admin portal, manual):
// register the Microsoft.Fabric / Microsoft.BotService / Microsoft.App resource
// providers, enable "Azure Private Link" and "Configure workspace-level inbound
// network rules" tenant settings, and enable Copilot / Azure OpenAI tenant
// settings the Operations Agent depends on.

targetScope = 'subscription'

@description('Azure region for every resource in this deployment. Must support Fabric capacities, Foundry VNet injection, and your chosen model. Avoid East US — the Operations Agent is not available there.')
param location string

@description('Name of the resource group to create.')
param resourceGroupName string = 'rg-pdm-fabric-foundry'

@description('UPNs of the Fabric capacity administrators. Include the account that will run setup/06_ops_agent.py — the Operations Agent inherits its creator\'s delegated identity.')
param fabricAdminMembers array

@description('Object ID of the operator who should get Key Vault Secrets Officer on this resource group (typically the person running the Python setup scripts from the jumpbox).')
param operatorPrincipalId string

param fabricCapacityName string = 'pdmopsf8'

param foundryAiServicesBaseName string = 'pdmopsai'
param foundryProjectName string = 'pdm-agents'
param foundryModelName string = 'gpt-4.1'
param foundryModelFormat string = 'OpenAI'
param foundryModelVersion string = '2025-04-14'
param foundryModelSkuName string = 'GlobalStandard'
param foundryModelCapacity int = 30

@description('Existing AI Search resource ID for the Foundry standard agent setup (BYO trio — required).')
param aiSearchResourceId string

@description('Existing Storage account resource ID for the Foundry standard agent setup.')
param azureStorageAccountResourceId string

@description('Existing Cosmos DB account resource ID for the Foundry standard agent setup.')
param azureCosmosDBAccountResourceId string

@description('Create a private Azure Container Registry for the hosted agent\'s build-and-push fallback path. Off by default; flip on only if REMOTE_BUILD (deploying from source) hits a platform-side ProvisioningError.')
param enableContainerRegistry bool = false

@description('Whether the Foundry account is created with public network access already enabled, rather than Disabled-then-toggled later. See infra/modules/foundry.bicep for why this matters — on by default because toggling an already-Disabled account has been observed to get stuck for 30+ minutes behind a fronting APIM layer.')
param publicNetworkAccessAtCreation bool = true

var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module network 'modules/network.bicep' = {
  name: 'network-deployment'
  scope: rg
  params: {
    location: location
  }
}

module capacity 'modules/fabric-capacity.bicep' = {
  name: 'fabric-capacity-deployment'
  scope: rg
  params: {
    location: location
    capacityName: fabricCapacityName
    adminMembers: fabricAdminMembers
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault-deployment'
  scope: rg
  params: {
    location: location
    vnetId: network.outputs.vnetId
    peSubnetId: network.outputs.peSubnetId
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring-deployment'
  scope: rg
  params: {
    location: location
  }
}

module jumpbox 'modules/aci-jumpbox.bicep' = {
  name: 'jumpbox-deployment'
  scope: rg
  params: {
    location: location
    containerSubnetId: network.outputs.containerSubnetId
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry-deployment'
  scope: rg
  params: {
    location: location
    aiServicesBaseName: foundryAiServicesBaseName
    firstProjectName: foundryProjectName
    modelName: foundryModelName
    modelFormat: foundryModelFormat
    modelVersion: foundryModelVersion
    modelSkuName: foundryModelSkuName
    modelCapacity: foundryModelCapacity
    vnetResourceId: network.outputs.vnetId
    agentSubnetName: network.outputs.agentSubnetName
    peSubnetName: network.outputs.peSubnetName
    aiSearchResourceId: aiSearchResourceId
    azureStorageAccountResourceId: azureStorageAccountResourceId
    azureCosmosDBAccountResourceId: azureCosmosDBAccountResourceId
    enableContainerRegistry: enableContainerRegistry
    publicNetworkAccessAtCreation: publicNetworkAccessAtCreation
  }
}

module operatorKvAccess 'modules/rbac.bicep' = {
  name: 'operator-kv-rbac-deployment'
  scope: rg
  params: {
    principalId: operatorPrincipalId
    roleDefinitionId: keyVaultSecretsOfficerRoleId
    principalType: 'User'
  }
}

// The jumpbox's own identity needs write access on the Foundry account
// (agent/toolbox registration) — Cognitive Services Contributor does NOT
// cover this; it's a dataAction only Foundry Project Manager grants.
module jumpboxFoundryAccess 'modules/aci-foundry-rbac.bicep' = {
  name: 'jumpbox-foundry-rbac-deployment'
  scope: rg
  params: {
    foundryAccountName: foundry.outputs.accountName
    principalId: jumpbox.outputs.principalId
  }
}

output location string = location
output resourceGroupName string = rg.name
output vnetId string = network.outputs.vnetId
output vnetName string = network.outputs.vnetName
output agentSubnetId string = network.outputs.agentSubnetId
output agentSubnetName string = network.outputs.agentSubnetName
output peSubnetId string = network.outputs.peSubnetId
output peSubnetName string = network.outputs.peSubnetName
output containerSubnetId string = network.outputs.containerSubnetId
output jumpboxContainerGroupName string = jumpbox.outputs.containerGroupName
output jumpboxPrincipalId string = jumpbox.outputs.principalId
output fabricCapacityId string = capacity.outputs.capacityId
output fabricCapacityName string = capacity.outputs.capacityName
output keyVaultName string = keyVault.outputs.vaultName
output keyVaultUri string = keyVault.outputs.vaultUri
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceId
output appInsightsConnectionString string = monitoring.outputs.connectionString
output foundryAccountName string = foundry.outputs.accountName
output foundryAccountId string = foundry.outputs.accountId
output foundryAccountEndpoint string = foundry.outputs.accountEndpoint
output foundryProjectName string = foundry.outputs.projectName
output foundryProjectId string = foundry.outputs.projectId
output foundryAcrName string = foundry.outputs.acrName
