// Thin wrapper around the vendored Microsoft sample in ./foundry-vendored, which
// provisions the network-secured (VNet-injected) Foundry account + project, its
// capability host, and the BYO Cosmos/Search/Storage role assignments.
//
// foundry-vendored/main.bicep declares NO outputs (checked against the copy on
// disk), so instead of hand-editing the vendored file, this wrapper re-derives the
// account/project names using the same deterministic formula the vendored file
// uses internally (see foundry-vendored/main.bicep, the `uniqueSuffix`/`accountName`
// vars near the top) and reads the resources back with `existing`.
//
// createAccountCapabilityHost is left false here on purpose — the vendored
// sample's own runbook creates the capability host via createCapHost.sh as a
// post-deploy step, not inline in main.bicep. Run that script after this module
// finishes. See infra/README.md phase 1.

@description('Location for the Foundry account/project. Must support VNet injection and your chosen model, and must be co-regional with the VNet.')
param location string

@description('Base name for the AI Foundry (Cognitive Services) account. The vendored module appends a 4-character deterministic suffix.')
param aiServicesBaseName string = 'pdmopsai'

param firstProjectName string = 'pdm-agents'
param projectDisplayName string = 'PdM Copilot project'
param projectDescription string = 'PdM Copilot — conversational investigator for the PdM Copilot'

param modelName string = 'gpt-4.1'
param modelFormat string = 'OpenAI'
param modelVersion string = '2025-04-14'
param modelSkuName string = 'GlobalStandard'
param modelCapacity int = 30

@description('Resource ID of the VNet created by modules/network.bicep. The vendored sample reuses its subnets rather than creating its own.')
param vnetResourceId string
param agentSubnetName string = 'snet-agent'
param peSubnetName string = 'snet-pe'

@description('Existing AI Search resource ID (required — standard agent setup refuses to build without all three BYO resources).')
param aiSearchResourceId string

@description('Existing Storage account resource ID.')
param azureStorageAccountResourceId string

@description('Existing Cosmos DB account resource ID.')
param azureCosmosDBAccountResourceId string

@description('Create a private Azure Container Registry for the hosted agent\'s build-and-push fallback path (used when deploying from source / REMOTE_BUILD hits a platform-side ProvisioningError). Off by default — the PdM Copilot only needs this as a fallback, not day to day.')
param enableContainerRegistry bool = false

@description('Whether the Foundry account is created with public network access already enabled, rather than Disabled-then-toggled later at deploy/publish time. Confirmed live: toggling an already-Disabled account can leave a fronting APIM layer stuck for 30+ minutes returning 403s even after the account resource itself correctly shows Enabled — creating it public from the start avoids that. deploy_hosted_agent.py / publish_teams.py still manage the Enabled->Disabled transition after the agent is deployed and published; this only controls the very first state.')
param publicNetworkAccessAtCreation bool = true

@description('Optional: resource group names for existing DNS zones, keyed by zone name. Leave values empty to let the vendored module create new zones.')
param existingDnsZones object = {
  'privatelink.services.ai.azure.com': ''
  'privatelink.openai.azure.com': ''
  'privatelink.cognitiveservices.azure.com': ''
  'privatelink.search.windows.net': ''
  'privatelink.blob.core.windows.net': ''
  'privatelink.documents.azure.com': ''
  // Only read when enableContainerRegistry is true, but the vendored ACR
  // module indexes this key unconditionally — must always be present, even
  // when the registry itself is off. Confirmed live: omitting it fails with
  // "The language expression property 'privatelink.azurecr.io' doesn't exist".
  'privatelink.azurecr.io': ''
}

// The vendored main.bicep's own default `dnsZoneNames` (its line ~157) always
// includes privatelink.azurecr.io, independent of enableContainerRegistry —
// disabling ACR does NOT remove it from the validation list, so it must be
// overridden here explicitly or validate-existing-resources.bicep fails
// looking up a zone key that was never in existingDnsZones. Confirmed by
// hitting exactly this failure on a live deploy, not just reading the source.
var dnsZoneNamesWithoutAcr = [
  'privatelink.services.ai.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.search.windows.net'
  'privatelink.blob.core.windows.net'
  'privatelink.documents.azure.com'
]

module foundryVendored 'foundry-vendored/main.bicep' = {
  name: 'foundry-vendored-deployment'
  params: {
    location: location
    aiServices: aiServicesBaseName
    firstProjectName: firstProjectName
    displayName: projectDisplayName
    projectDescription: projectDescription
    modelName: modelName
    modelFormat: modelFormat
    modelVersion: modelVersion
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    existingVnetResourceId: vnetResourceId
    agentSubnetName: agentSubnetName
    peSubnetName: peSubnetName
    reuseExistingSubnets: true
    aiSearchResourceId: aiSearchResourceId
    azureStorageAccountResourceId: azureStorageAccountResourceId
    azureCosmosDBAccountResourceId: azureCosmosDBAccountResourceId
    existingDnsZones: existingDnsZones
    dnsZoneNames: dnsZoneNamesWithoutAcr
    createAccountCapabilityHost: false
    enableContainerRegistry: enableContainerRegistry
    publicNetworkAccessAtCreation: publicNetworkAccessAtCreation
  }
}

// Matches foundry-vendored/main.bicep's own `uniqueSuffix`/`accountName`/
// `projectName` vars exactly (its line ~53 and ~168) — confirmed against a
// live deployment, where the actual project name came back as
// 'pdm-agents4fgm', not the bare 'pdm-agents' param value. Bicep's
// `existing` resource declarations don't validate at deploy time, so getting
// this wrong silently fabricates an ID for a resource that was never there,
// rather than failing loudly.
var uniqueSuffix = substring(uniqueString(resourceGroup().id), 0, 4)
var accountName = toLower('${aiServicesBaseName}${uniqueSuffix}')
var actualProjectName = toLower('${firstProjectName}${uniqueSuffix}')
var acrName = toLower('acr${uniqueSuffix}')

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
  dependsOn: [
    foundryVendored
  ]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: actualProjectName
  dependsOn: [
    foundryVendored
  ]
}

output accountName string = account.name
output accountId string = account.id
output accountEndpoint string = account.properties.endpoint
output accountPrincipalId string = account.identity.principalId
output projectName string = project.name
output projectId string = project.id
output acrName string = enableContainerRegistry ? acrName : ''
