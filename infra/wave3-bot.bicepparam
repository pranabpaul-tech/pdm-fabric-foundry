using './wave3-bot.bicep'

// Fill these after foundry/agent.py has created and exercised the PdM
// Copilot (see state.json).

param botName = 'bot-pdm-orchestrator'
param botDisplayName = 'PdM Copilot'
param msaAppId = 'CHANGE_ME-agent-client-id'
param activityEndpoint = 'https://CHANGE_ME.services.ai.azure.com/api/projects/CHANGE_ME/agents/CHANGE_ME/endpoint/protocols/activityProtocol?api-version=2025-05-15-preview'
