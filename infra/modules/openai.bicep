param name string
param location string
param tags object

resource aoai 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
  }
}

resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: aoai
  name: 'gpt-4o'
  sku: {
    name: 'Standard'
    capacity: 80  // 80K TPM
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-05-13'
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: aoai
  name: 'text-embedding-3-large'
  dependsOn: [gpt4oDeployment]   // deploy sequentially to avoid capacity conflicts
  sku: {
    name: 'Standard'
    capacity: 120   // 120K TPM — embeddings are cheap, need throughput for indexing
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

output endpoint string = aoai.properties.endpoint
output id string = aoai.id
