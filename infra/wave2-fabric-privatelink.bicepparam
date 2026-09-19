using './wave2-fabric-privatelink.bicep'

// Fill these from the Wave 1 deployment outputs and from
// state.json after setup/01_workspace.py has run.

param workspaceId = 'CHANGE_ME-workspace-guid'
param location = 'swedencentral'
param vnetId = '/subscriptions/CHANGE_ME/resourceGroups/rg-pdm-fabric-foundry/providers/Microsoft.Network/virtualNetworks/vnet-pdmops'
param peSubnetId = '/subscriptions/CHANGE_ME/resourceGroups/rg-pdm-fabric-foundry/providers/Microsoft.Network/virtualNetworks/vnet-pdmops/subnets/snet-pe'
