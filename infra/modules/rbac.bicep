// Generic resource-group-scoped role assignment. Good enough for a demo/pilot —
// production hardening should narrow scope to the specific Key Vault / storage
// resource rather than the whole resource group.

@description('Object ID of the user, group, service principal or managed identity to grant the role to.')
param principalId string

@description('Role definition GUID (the segment after /roleDefinitions/), e.g. Key Vault Secrets Officer = b86a8fe4-44ce-4948-aee5-eccb2c155cd7.')
param roleDefinitionId string

@allowed(['User', 'Group', 'ServicePrincipal'])
param principalType string = 'User'

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, roleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: principalType
  }
}

output roleAssignmentId string = roleAssignment.id
