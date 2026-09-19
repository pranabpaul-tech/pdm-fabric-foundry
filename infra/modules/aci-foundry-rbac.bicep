// Grants the ACI jumpbox's system-assigned identity the "Foundry Project
// Manager" role on the Foundry account — confirmed live that "Cognitive
// Services Contributor" does NOT cover agent/toolbox writes
// (Microsoft.CognitiveServices/accounts/AIServices/agents/write was denied);
// "Foundry Project Manager" grants Microsoft.CognitiveServices/accounts/projects/*,
// which does.

param foundryAccountName string
param principalId string

var foundryProjectManagerRoleId = 'eadc314b-1a2d-4efa-be10-5d325db5065e'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, foundryProjectManagerRoleId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryProjectManagerRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
