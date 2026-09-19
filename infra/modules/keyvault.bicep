@description('Location for the Key Vault.')
param location string

// Key Vault names are capped at 24 characters — 'kv-pdmops-' (12) plus a
// 13-char uniqueString() blows that budget by 1, so this uses a shorter prefix.
param vaultName string = 'kv-pdm-${uniqueString(resourceGroup().id)}'

param vnetId string
param peSubnetId string

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

module privateEndpoint 'private-endpoint.bicep' = {
  name: 'kv-pe-deployment'
  params: {
    name: 'pe-${vaultName}'
    location: location
    subnetId: peSubnetId
    targetResourceId: kv.id
    groupIds: ['vault']
    dnsZoneName: 'privatelink.vaultcore.azure.net'
    vnetId: vnetId
  }
}

output vaultName string = kv.name
output vaultUri string = kv.properties.vaultUri
output vaultId string = kv.id
