param envName string
param backendAppName string
param frontendAppName string
param location string
param tags object
param acrLoginServer string
param acrName string
@secure()
param appInsightsConnectionString string
param keyVaultName string
param keyVaultUri string
param logAnalyticsWorkspaceName string
param logAnalyticsCustomerId string
param cosmosEndpoint string
param searchEndpoint string
param aoaiEndpoint string
param jiraBaseUrl string
param jiraProjectKey string
param jiraUserEmail string
param entraTenantId string
param entraClientId string
// Non-secret deployment config
param aoaiDeployment string = 'gpt-4o'
param aoaiEmbeddingDeployment string = 'text-embedding-3-large'
param aoaiApiVersion string = '2024-05-01-preview'
param searchCodeIndex string = 'code-index'
param searchDocsIndex string = 'docs-index'
param searchVectorDimensions string = '3072'
// LLM provider — 'openai' uses OpenAI direct; 'azure' uses Azure OpenAI
param llmProvider string = 'openai'
param openaiModel string = 'gpt-4o'
// CORS allowed origins for the backend. Defaults to ["*"] on first deploy.
// After provisioning, set FRONTEND_HOSTNAME in azd env and re-provision to lock this down.
param corsOrigins string = '["*"]'

// ── Log Analytics reference — listKeys() called here to avoid secret module outputs ──
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

// ── ACA managed environment ───────────────────────────────────────────────────
resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  tags: tags
  properties: {
    // Route container logs to Log Analytics — without this, stderr/stdout is lost
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    daprAIConnectionString: appInsightsConnectionString
  }
}

// ── User-assigned managed identity (ACR pull + KV Secrets User) ──────────────
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${envName}-identity'
  location: location
  tags: tags
}

// ACR pull — scoped to the specific registry, not the whole resource group
resource acrResource 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrResource.id, identity.id, acrPullRoleId)
  scope: acrResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Secrets User — scoped to the specific vault
resource kvResource 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kvResource.id, identity.id, kvSecretsUserRoleId)
  scope: kvResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Backend Container App ─────────────────────────────────────────────────────
resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: backendAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        // Allow larger request bodies for chat payloads
        clientCertificateMode: 'ignore'
      }
      registries: [{
        server: acrLoginServer
        identity: identity.id
      }]
      secrets: [
        { name: 'appinsights-cs', value: appInsightsConnectionString }
      ]
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 10
        rules: [{
          name: 'http-scaler'
          http: { metadata: { concurrentRequests: '20' } }
        }]
      }
      containers: [{
        name: 'backend'
        image: '${acrLoginServer}/${backendAppName}:latest'
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: [
          // ── Non-secret config ─────────────────────────────────────────────
          { name: 'LLM_PROVIDER', value: llmProvider }
          { name: 'OPENAI_MODEL', value: openaiModel }
          { name: 'AZURE_OPENAI_ENDPOINT', value: aoaiEndpoint }
          { name: 'AZURE_OPENAI_DEPLOYMENT', value: aoaiDeployment }
          { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: aoaiEmbeddingDeployment }
          { name: 'AZURE_OPENAI_API_VERSION', value: aoaiApiVersion }
          { name: 'AZURE_SEARCH_ENDPOINT', value: searchEndpoint }
          { name: 'AZURE_SEARCH_CODE_INDEX', value: searchCodeIndex }
          { name: 'AZURE_SEARCH_DOCS_INDEX', value: searchDocsIndex }
          { name: 'AZURE_SEARCH_VECTOR_DIMENSIONS', value: searchVectorDimensions }
          { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
          { name: 'JIRA_BASE_URL', value: jiraBaseUrl }
          { name: 'JIRA_PROJECT_KEY', value: jiraProjectKey }
          { name: 'JIRA_USER_EMAIL', value: jiraUserEmail }
          { name: 'ENTRA_TENANT_ID', value: entraTenantId }
          { name: 'ENTRA_CLIENT_ID', value: entraClientId }
          // KEY_VAULT_URL enables the app to pull secrets (keys, tokens) at startup
          { name: 'KEY_VAULT_URL', value: keyVaultUri }
          { name: 'APP_ENVIRONMENT', value: 'production' }
          // CORS: defaults to ["*"] on first deploy; set FRONTEND_HOSTNAME in azd env
          // and re-provision to restrict to the actual frontend origin.
          { name: 'CORS_ORIGINS', value: corsOrigins }
          // ── Secret refs ───────────────────────────────────────────────────
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-cs' }
        ]
      }]
    }
  }
}

// ── Frontend Container App ────────────────────────────────────────────────────
// The frontend is an nginx container that serves the built React SPA and
// proxies /api/* to the backend. NGINX_BACKEND_URL is resolved from the
// backend resource's FQDN — ARM handles ordering automatically.
resource frontend 'Microsoft.App/containerApps@2024-03-01' = {
  name: frontendAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    configuration: {
      ingress: {
        external: true
        targetPort: 80
        transport: 'http'
      }
      registries: [{
        server: acrLoginServer
        identity: identity.id
      }]
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 5 }
      containers: [{
        name: 'frontend'
        image: '${acrLoginServer}/${frontendAppName}:latest'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          // nginx.conf template uses this to proxy /api/ → backend
          { name: 'NGINX_BACKEND_URL', value: 'https://${backend.properties.configuration.ingress.fqdn}' }
        ]
      }]
    }
  }
}

output backendFqdn string = backend.properties.configuration.ingress.fqdn
output frontendFqdn string = frontend.properties.configuration.ingress.fqdn
output identityId string = identity.id
output identityClientId string = identity.properties.clientId
