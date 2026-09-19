using './main.bicep'

// Values come from the azd environment (set by infra/hooks/preprovision.*), with
// safe literals as fallbacks for a plain `az deployment sub create`.

// swedencentral: hosted-agent invocation routes register within seconds there. westus
// was observed to take 15-40+ minutes (or never) in the reference deployments, and
// the Operations Agent is not available in eastus. Confirm Fabric capacity quota in
// your region first (see DEPLOYMENT_PLAN_v2.md section 5) before changing this.
param location = 'swedencentral'
param resourceGroupName = readEnvironmentVariable('AZURE_RESOURCE_GROUP_NAME', 'rg-pdm-fabric-foundry')

// Fabric capacity administrators. Include whoever runs setup/06_ops_agent.py - the
// Operations Agent runs under its creator's delegated identity.
param fabricAdminMembers = [
  readEnvironmentVariable('FABRIC_ADMIN_UPN', 'CHANGE_ME@yourtenant.onmicrosoft.com')
]

// az ad signed-in-user show --query id -o tsv
param operatorPrincipalId = readEnvironmentVariable('OPERATOR_OBJECT_ID', '00000000-0000-0000-0000-000000000000')

param fabricCapacityName = readEnvironmentVariable('FABRIC_CAPACITY_NAME', 'pdmopsf8')

param foundryAiServicesBaseName = readEnvironmentVariable('FOUNDRY_AI_SERVICES_BASE_NAME', 'pdmopsai')
param foundryProjectName = 'pdm-agents'
param foundryModelName = 'gpt-4.1'
param foundryModelFormat = 'OpenAI'
param foundryModelVersion = '2025-04-14'
param foundryModelSkuName = 'GlobalStandard'
param foundryModelCapacity = 30

// Created by wave0-byo-resources.bicep (deployment name: wave0-byo-deployment). The
// preprovision hook copies them from that deployment's outputs into the azd env.
param aiSearchResourceId = readEnvironmentVariable('AI_SEARCH_RESOURCE_ID', '')
param azureStorageAccountResourceId = readEnvironmentVariable('AZURE_STORAGE_RESOURCE_ID', '')
param azureCosmosDBAccountResourceId = readEnvironmentVariable('AZURE_COSMOS_RESOURCE_ID', '')

// Agents are deployed from an image built with ACR Tasks (DEPLOYMENT_PLAN_v2.md 9.3).
param enableContainerRegistry = true
// Must stay true through agent deploy + Teams publish; publish_teams.py flips it back.
param publicNetworkAccessAtCreation = true
