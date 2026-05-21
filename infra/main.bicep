targetScope = 'resourceGroup'

@description('Environment name (dev, staging, production)')
param environmentName string

@description('Primary Azure region')
param location string = resourceGroup().location

@description('Jira base URL (e.g. https://your-org.atlassian.net)')
param jiraBaseUrl string

@description('Jira project key (e.g. PROJ)')
param jiraProjectKey string

@description('Jira user email for API authentication')
param jiraUserEmail string

@description('Azure Entra tenant ID')
param entraTenantId string

@description('Azure Entra client ID (app registration)')
param entraClientId string

@description('APIM publisher email')
param publisherEmail string = 'admin@example.com'

@description('Allowed CORS origins for the backend. Set to the frontend URL after first deploy.')
param corsOrigins string = '["*"]'

@description('LLM provider: openai (direct) or azure (Azure OpenAI)')
param llmProvider string = 'openai'

@description('OpenAI model name when using openai provider')
param openaiModel string = 'gpt-4o'

var prefix = 'eai-${environmentName}'
var tags = { environment: environmentName, project: 'enterprise-ai-agent' }

// ── Key Vault ─────────────────────────────────────────────────────────────────
module kv 'modules/keyvault.bicep' = {
  name: 'kv'
  params: {
    name: '${prefix}-kv'
    location: location
    tags: tags
  }
}

// ── Azure Container Registry ──────────────────────────────────────────────────
module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    name: replace('${prefix}acr', '-', '')
    location: location
    tags: tags
  }
}

// ── Azure OpenAI ──────────────────────────────────────────────────────────────
// Only provisioned when llmProvider == 'azure'. When using OpenAI direct (llmProvider == 'openai')
// this module is skipped — the subscription does not need Azure OpenAI access.
module aoai 'modules/openai.bicep' = if (llmProvider == 'azure') {
  name: 'aoai'
  params: {
    name: '${prefix}-aoai'
    location: location
    tags: tags
  }
}

// ── Azure AI Search ───────────────────────────────────────────────────────────
module search 'modules/search.bicep' = {
  name: 'search'
  params: {
    name: '${prefix}-search'
    location: location
    tags: tags
  }
}

// ── Cosmos DB ─────────────────────────────────────────────────────────────────
module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    name: '${prefix}-cosmos'
    location: location
    tags: tags
  }
}

// ── Application Insights + Log Analytics ─────────────────────────────────────
module appInsights 'modules/appinsights.bicep' = {
  name: 'appinsights'
  params: {
    name: '${prefix}-ai'
    location: location
    tags: tags
  }
}

// ── Container Apps Environment + Apps ─────────────────────────────────────────
module aca 'modules/containerapps.bicep' = {
  name: 'aca'
  params: {
    envName: '${prefix}-cae'
    backendAppName: '${prefix}-backend'
    frontendAppName: '${prefix}-frontend'
    location: location
    tags: tags
    acrLoginServer: acr.outputs.loginServer
    acrName: acr.outputs.name
    appInsightsConnectionString: appInsights.outputs.connectionString
    keyVaultName: kv.outputs.name
    keyVaultUri: kv.outputs.uri
    logAnalyticsWorkspaceName: appInsights.outputs.workspaceName
    logAnalyticsCustomerId: appInsights.outputs.workspaceCustomerId
    cosmosEndpoint: cosmos.outputs.endpoint
    searchEndpoint: search.outputs.endpoint
    aoaiEndpoint: llmProvider == 'azure' ? aoai!.outputs.endpoint : ''
    jiraBaseUrl: jiraBaseUrl
    jiraProjectKey: jiraProjectKey
    jiraUserEmail: jiraUserEmail
    entraTenantId: entraTenantId
    entraClientId: entraClientId
    corsOrigins: corsOrigins
    llmProvider: llmProvider
    openaiModel: openaiModel
  }
}

// ── API Management ────────────────────────────────────────────────────────────
module apim 'modules/apim.bicep' = {
  name: 'apim'
  params: {
    name: '${prefix}-apim'
    location: location
    tags: tags
    backendUrl: 'https://${aca.outputs.backendFqdn}'
    entraTenantId: entraTenantId
    entraClientId: entraClientId
    publisherEmail: publisherEmail
  }
}

output backendUrl string = 'https://${aca.outputs.backendFqdn}'
output frontendUrl string = 'https://${aca.outputs.frontendFqdn}'
output apimGatewayUrl string = apim.outputs.gatewayUrl
output keyVaultName string = kv.outputs.name
