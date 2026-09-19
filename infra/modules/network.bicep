@description('Location for the VNet and its subnets.')
param location string

@description('VNet name.')
param vnetName string = 'vnet-pdmops'

param vnetAddressPrefix string = '10.10.0.0/16'

@description('Private endpoints subnet — Fabric workspace private link, Key Vault, and the Foundry BYO trio all land here.')
param peSubnetPrefix string = '10.10.0.0/24'
param peSubnetName string = 'snet-pe'

@description('Foundry agent subnet — must stay delegated to Microsoft.App/environments and dedicated to one Foundry account.')
param agentSubnetPrefix string = '10.10.1.0/24'
param agentSubnetName string = 'snet-agent'

@description('Container Instance subnet — delegated to Microsoft.ContainerInstance/containerGroups. Hosts the jumpbox, reachable via `az container exec` (Azure control plane — no RDP/Bastion needed).')
param containerSubnetPrefix string = '10.10.3.0/24'
param containerSubnetName string = 'snet-container'

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: agentSubnetName
        properties: {
          addressPrefix: agentSubnetPrefix
          delegations: [
            {
              name: 'foundryAgentDelegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: containerSubnetName
        properties: {
          addressPrefix: containerSubnetPrefix
          delegations: [
            {
              name: 'aciDelegation'
              properties: {
                serviceName: 'Microsoft.ContainerInstance/containerGroups'
              }
            }
          ]
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output peSubnetId string = vnet.properties.subnets[0].id
output peSubnetName string = peSubnetName
output agentSubnetId string = vnet.properties.subnets[1].id
output agentSubnetName string = agentSubnetName
output containerSubnetId string = vnet.properties.subnets[2].id
output containerSubnetName string = containerSubnetName
