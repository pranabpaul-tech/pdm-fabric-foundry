// PdM Copilot — Wave 0 (not in the original plan's 3-wave numbering)
// Minimal AI Search / Storage / Cosmos DB so Wave 1's Foundry standard agent
// setup has the BYO trio it refuses to deploy without. Deployed once, ahead
// of everything else — main.bicep takes their resource IDs as parameters
// rather than creating them itself, so you can swap in existing resources
// later without touching main.bicep.

@description('Location — should match Wave 1\'s location. See README "Region choice matters" for why swedencentral, not westus, is the default.')
param location string = 'swedencentral'

param searchServiceName string = 'srch-pdmops-${uniqueString(resourceGroup().id)}'
param storageAccountName string = 'stpdmops${uniqueString(resourceGroup().id)}'
param cosmosAccountName string = 'cosmos-pdmops-${uniqueString(resourceGroup().id)}'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchServiceName
  location: location
  sku: {
    name: 'standard'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled' // Wave 1's private-endpoint-and-dns module locks this down after connecting
    // Foundry's standard agent setup connects to Search with authType=AAD —
    // apiKeyOnly (the resource default) makes every agent call fail with a 403.
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
  }
}

output aiSearchResourceId string = search.id
output azureStorageAccountResourceId string = storage.id
output azureCosmosDBAccountResourceId string = cosmos.id
