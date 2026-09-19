// Generic single-zone private endpoint + private DNS zone + VNet link + zone group.
// For a target that needs more than one DNS zone on the same endpoint (the Fabric
// tenant-level private link needs three), don't reuse this module — see
// infra/modules/fabric-tenant-privatelink.bicep for that shape instead.

@description('Name for the private endpoint resource.')
param name string

@description('Azure region for the private endpoint NIC (must match the VNet\'s region — private endpoints are regional, unlike the DNS zone or the service they target).')
param location string

@description('Subnet to place the private endpoint NIC in (normally snet-pe).')
param subnetId string

@description('Resource ID of the service being connected to privately.')
param targetResourceId string

@description('Private-link sub-resource name(s) for the target, e.g. ["vault"], ["blob"], ["workspace"].')
param groupIds array

@description('Private DNS zone name to create/reuse and link, e.g. privatelink.vaultcore.azure.net')
param dnsZoneName string

@description('VNet resource ID to link the DNS zone to.')
param vnetId string

resource dnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: dnsZoneName
  location: 'global'
}

resource dnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsZone
  name: 'link-${uniqueString(vnetId)}'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnetId
    }
    registrationEnabled: false
  }
}

resource pe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: name
  location: location
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${name}-connection'
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: groupIds
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: pe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: replace(dnsZoneName, '.', '-')
        properties: {
          privateDnsZoneId: dnsZone.id
        }
      }
    ]
  }
}

output privateEndpointId string = pe.id
output dnsZoneId string = dnsZone.id
