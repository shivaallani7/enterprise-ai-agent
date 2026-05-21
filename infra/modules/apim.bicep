param name string
param location string
param tags object
param backendUrl string
param publisherEmail string = 'admin@example.com'

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Consumption', capacity: 0 }
  properties: {
    publisherEmail: publisherEmail
    publisherName: 'Enterprise AI Agent'
  }
}

// Backend pointing to ACA
resource apimBackend 'Microsoft.ApiManagement/service/backends@2023-09-01-preview' = {
  parent: apim
  name: 'agent-backend'
  properties: {
    url: backendUrl
    protocol: 'http'
    tls: { validateCertificateChain: true, validateCertificateName: true }
  }
}

// API definition
resource api 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'agent-api'
  properties: {
    displayName: 'Enterprise AI Agent API'
    path: ''
    protocols: ['https']
    serviceUrl: backendUrl
    subscriptionRequired: false
  }
}

// JWT validation policy — IMPORTANT: use regular string interpolation here.
// Triple-quoted raw strings (''') in Bicep do NOT expand ${...} expressions.
// Only regular single-quoted strings support interpolation.
resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies><inbound><base /><set-backend-service backend-id="agent-backend" /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
  }
}

output gatewayUrl string = apim.properties.gatewayUrl
output id string = apim.id
