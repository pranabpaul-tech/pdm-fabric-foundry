// PdM Copilot — Wave 3
// Bot Service + Teams channel for the Foundry "PdM Copilot". Run this
// AFTER foundry/agent.py has created the agent — msaAppId and activityEndpoint
// come from that agent's own instance_identity.client_id and its
// activityProtocol URL, not from anything created in Wave 1 or 2.
//
// SingleTenant + the agent's own client ID is the pattern the Foundry
// publish-to-Teams doc uses for its native Foundry-hosted agents — this is the
// exact template from learn.microsoft.com/azure/foundry/agents/how-to/
// publish-copilot-virtual-network (step 2), confirmed live on 2026-09-11.
// Deliberately NOT the UserAssignedMSI pattern used for a hand-rolled bot host
// (see MyM365Agent1/M365Agent/infra/botRegistration/azurebot.bicep for that
// alternative) — the plan recommends against standing up a custom bot host here,
// since Foundry's native publish reaches the same Teams surface with no
// container to run.
//
// The deploying identity needs "Azure Bot Service Contributor" on this resource
// group. Register the Microsoft.BotService provider first if this is the first
// bot in the subscription: az provider register --namespace Microsoft.BotService
//
// After this deploys, run foundry/publish_teams.py — it does the PATCH
// (protocol_configuration / authorization_schemes) and the microsoft365/publish
// call that the Bicep resource alone doesn't cover.
//
// publicNetworkAccess is 'Enabled' on this resource — confirmed against a live
// reference deployment (pranabpaul-tech/foundry-iq-v2, same subscription,
// same VNet-injected-Foundry-agent-behind-a-Bot-Service architecture) that
// 'Disabled' here blocks channel client-facing traffic outright (tested via
// Direct Line: NetworkDenied), not just inbound calls to the Foundry side.
// The actual network isolation in this design lives entirely on the Foundry
// account (publicNetworkAccess: Disabled there, with its own
// source-IP-filtered exception for just the Activity Protocol route) — this
// Bot Service resource is meant to be reachable, matching Microsoft's own
// publish-copilot-virtual-network guide's premise that Bot Service/Teams
// infrastructure reaches the agent through that exception, not through our VNet.

@description('Bot Service resource name.')
param botName string

@description('Display name shown in Teams.')
param botDisplayName string = 'PdM Copilot'

@description('The Foundry agent\'s own client ID (instance_identity.client_id from foundry/agent.py).')
param msaAppId string

@description('The Foundry agent\'s activityProtocol endpoint URL, e.g. https://{account}.services.ai.azure.com/api/projects/{project}/agents/{agent}/endpoint/protocols/activityProtocol?api-version=2025-05-15-preview')
param activityEndpoint string

param tenantId string = tenant().tenantId

resource botService 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botName
  kind: 'azurebot'
  location: 'global'
  sku: {
    name: 'F0'
  }
  properties: {
    displayName: botDisplayName
    endpoint: activityEndpoint
    msaAppId: msaAppId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
    publicNetworkAccess: 'Enabled'
  }
}

resource teamsChannel 'Microsoft.BotService/botServices/channels@2021-03-01' = {
  parent: botService
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      // Confirmed live: this resource deploys fine with acceptedTerms
      // defaulting to false, and the channel shows isEnabled/provisioningState
      // Succeeded regardless — nothing in the deployment surfaces that the
      // agent won't actually respond in Teams until you check this specific
      // field. Set it explicitly rather than relying on the API default.
      acceptedTerms: true
      isEnabled: true
    }
  }
}

output botServiceId string = botService.id
output botServiceName string = botService.name
