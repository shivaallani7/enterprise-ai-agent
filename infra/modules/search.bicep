param name string
param location string
param tags object

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'standard' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'standard'  // Enables semantic ranking
    publicNetworkAccess: 'Enabled'
  }
}

output endpoint string = 'https://${name}.search.windows.net'
output id string = search.id
