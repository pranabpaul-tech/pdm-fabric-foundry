// PdM Copilot — Wave 2
// Workspace-level Fabric private link. Run this AFTER setup/01_workspace.py has
// created the Fabric workspace — workspaceId below comes from that script's
// state.json output, not from anything in Wave 1.
//
// Before running this for the first time in the subscription, re-register the
// Microsoft.Fabric resource provider (workspace-level PL has its own
// registration flag, separate from tenant-level PL):
//   az provider register --namespace Microsoft.Fabric
//
// A freshly created Fabric capacity can take up to 24 hours to appear in the
// private DNS zone. If `nslookup {workspaceId}.z{xy}.w.api.fabric.microsoft.com`
// from the jumpbox doesn't resolve to a private IP yet, that's most likely why —
// not a misconfiguration. Re-check before assuming this deployment is broken.

@description('Fabric workspace ID (GUID) created by setup/01_workspace.py.')
param workspaceId string

@description('Microsoft Entra tenant ID.')
param tenantId string = tenant().tenantId

@description('Azure region for the private endpoint NIC — should match Wave 1\'s location.')
param location string

@description('VNet resource ID from Wave 1 output vnetId.')
param vnetId string

@description('snet-pe subnet resource ID from Wave 1 output peSubnetId.')
param peSubnetId string

param privateLinkServiceName string = 'pl-fabric-workspace-pdmops'
param privateEndpointName string = 'pe-fabric-workspace-pdmops'

resource workspacePLS 'Microsoft.Fabric/privateLinkServicesForFabric@2024-06-01' = {
  name: privateLinkServiceName
  location: 'global'
  properties: {
    tenantId: tenantId
    workspaceId: workspaceId
  }
}

module privateEndpoint 'modules/private-endpoint.bicep' = {
  name: 'fabric-workspace-pe-deployment'
  params: {
    name: privateEndpointName
    location: location
    subnetId: peSubnetId
    targetResourceId: workspacePLS.id
    groupIds: ['workspace']
    dnsZoneName: 'privatelink.fabric.microsoft.com'
    vnetId: vnetId
  }
}

output privateLinkServiceId string = workspacePLS.id
output privateEndpointId string = privateEndpoint.outputs.privateEndpointId
