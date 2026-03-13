targetScope = 'resourceGroup'

@description('Global resource prefix. Keep this short and lowercase.')
param prefix string = 'abopolicy'

@description('Environment label appended to resource names.')
param environmentName string = 'dev'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Container image to deploy into Azure Container Apps.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Foundry project endpoint base URL in services.ai.azure.com format.')
param foundryProjectEndpoint string = ''

@description('Foundry project name.')
param foundryProjectName string = ''

@description('Foundry AI services resource name.')
param foundryResourceName string = ''

@description('Foundry workflow name to invoke.')
param foundryWorkflowName string = ''

@description('Foundry workflow endpoint URL for chatbot requests.')
param foundryWorkflowEndpoint string = ''

@description('API version used for workflow invocation endpoint patterns.')
param foundryWorkflowApiVersion string = '2025-11-15-preview'

@description('OAuth scope used by the app when requesting Foundry access tokens.')
param foundryScope string = 'https://ai.azure.com/.default'

@description('Allow unauthenticated chat requests. Keep false for Entra-protected environments.')
param allowAnonymous bool = false

@description('Microsoft Entra tenant ID for access token validation.')
param entraTenantId string = ''

@description('Client ID of the Entra application used by the web UI.')
param entraClientId string = ''

@description('Audience/App ID URI expected on access tokens by the backend API.')
param entraAudience string = ''

@description('Cosmos DB database name for chat state.')
param cosmosDatabaseName string = 'policy-chatbot-db'

@description('Cosmos DB container for sessions, partitioned by user identity.')
param cosmosSessionsContainerName string = 'chat-sessions'

@description('Cosmos DB container for messages, partitioned by session ID.')
param cosmosMessagesContainerName string = 'chat-messages'

@description('Optional Foundry API key. Leave empty when using managed identity.')
@secure()
param foundryApiKey string = ''

var suffix = toLower('${prefix}-${environmentName}')
var containerAppName = toLower('${suffix}-chatbot')
var managedEnvironmentName = toLower('${suffix}-cae')
var logAnalyticsName = toLower('${suffix}-law')
var appInsightsName = toLower('${suffix}-appi')
var acrName = replace(toLower('${prefix}${environmentName}acr'), '-', '')
var cosmosName = toLower('${suffix}-cosmos')
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var foundryApiSecret = empty(foundryApiKey)
  ? []
  : [
      {
        name: 'foundry-api-key'
        value: foundryApiKey
      }
    ]

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2023-04-15' = {
  name: cosmosName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableAutomaticFailover: false
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    capabilities: []
    disableLocalAuth: false
    enableFreeTier: false
    publicNetworkAccess: 'Enabled'
  }
}

resource cosmosSqlDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2023-04-15' = {
  parent: cosmosAccount
  name: cosmosDatabaseName
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
    options: {}
  }
}

resource cosmosSessionsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-04-15' = {
  parent: cosmosSqlDb
  name: cosmosSessionsContainerName
  properties: {
    resource: {
      id: cosmosSessionsContainerName
      partitionKey: {
        paths: [
          '/userId'
        ]
        kind: 'Hash'
      }
      defaultTtl: 2592000
    }
    options: {
      autoscaleSettings: {
        maxThroughput: 4000
      }
    }
  }
}

resource cosmosMessagesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2023-04-15' = {
  parent: cosmosSqlDb
  name: cosmosMessagesContainerName
  properties: {
    resource: {
      id: cosmosMessagesContainerName
      partitionKey: {
        paths: [
          '/sessionId'
        ]
        kind: 'Hash'
      }
      defaultTtl: 2592000
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
        ]
        compositeIndexes: [
          [
            {
              path: '/sessionId'
              order: 'ascending'
            }
            {
              path: '/createdAt'
              order: 'ascending'
            }
          ]
        ]
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: 4000
      }
    }
  }
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: managedEnvironmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

var useAcrRegistry = contains(toLower(containerImage), '${toLower(acrName)}.azurecr.io')
var containerAppSecrets = concat(
  foundryApiSecret,
  useAcrRegistry
    ? [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    : []
)

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        allowInsecure: false
      }
      registries: useAcrRegistry
        ? [
            {
              server: acr.properties.loginServer
              username: acr.listCredentials().username
              passwordSecretRef: 'acr-password'
            }
          ]
        : []
      secrets: containerAppSecrets
    }
    template: {
      containers: [
        {
          name: 'policy-chatbot'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'APP_ENV'
              value: environmentName
            }
            {
              name: 'ALLOW_ANONYMOUS'
              value: string(allowAnonymous)
            }
            {
              name: 'ENTRA_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'ENTRA_CLIENT_ID'
              value: entraClientId
            }
            {
              name: 'ENTRA_AUDIENCE'
              value: entraAudience
            }
            {
              name: 'FOUNDRY_PROJECT_ENDPOINT'
              value: foundryProjectEndpoint
            }
            {
              name: 'FOUNDRY_PROJECT_NAME'
              value: foundryProjectName
            }
            {
              name: 'FOUNDRY_RESOURCE_NAME'
              value: foundryResourceName
            }
            {
              name: 'FOUNDRY_WORKFLOW_NAME'
              value: foundryWorkflowName
            }
            {
              name: 'FOUNDRY_WORKFLOW_ID'
              value: foundryWorkflowName
            }
            {
              name: 'FOUNDRY_WORKFLOW_ENDPOINT'
              value: foundryWorkflowEndpoint
            }
            {
              name: 'FOUNDRY_WORKFLOW_API_VERSION'
              value: foundryWorkflowApiVersion
            }
            {
              name: 'FOUNDRY_WORKFLOW_BASE_ENDPOINT'
              value: empty(foundryResourceName)
                ? ''
                : 'https://${foundryResourceName}.services.ai.azure.com'
            }
            {
              name: 'FOUNDRY_WORKFLOW_RUN_API_VERSION'
              value: foundryWorkflowApiVersion
            }
            {
              name: 'FOUNDRY_WORKFLOW_SCOPE'
              value: 'https://ml.azure.com/.default'
            }
            {
              name: 'FOUNDRY_SUBSCRIPTION_ID'
              value: subscription().subscriptionId
            }
            {
              name: 'FOUNDRY_RESOURCE_GROUP'
              value: resourceGroup().name
            }
            {
              name: 'FOUNDRY_WORKSPACE_NAME'
              value: foundryProjectName
            }
            {
              name: 'FOUNDRY_SCOPE'
              value: foundryScope
            }
            {
              name: 'COSMOS_ENABLED'
              value: 'true'
            }
            {
              name: 'COSMOS_ENDPOINT'
              value: cosmosAccount.properties.documentEndpoint
            }
            {
              name: 'COSMOS_DATABASE'
              value: cosmosDatabaseName
            }
            {
              name: 'COSMOS_SESSIONS_CONTAINER'
              value: cosmosSessionsContainerName
            }
            {
              name: 'COSMOS_MESSAGES_CONTAINER'
              value: cosmosMessagesContainerName
            }
            {
              name: 'APPINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
            {
              name: 'FOUNDRY_API_KEY'
              value: foundryApiKey
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (useAcrRegistry) {
  name: guid(acr.id, containerApp.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource cosmosDataContributorRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2023-04-15' existing = {
  parent: cosmosAccount
  name: cosmosDataContributorRoleId
}

resource cosmosRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2023-04-15' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, containerApp.id, 'cosmos-contributor')
  properties: {
    principalId: containerApp.identity.principalId
    roleDefinitionId: cosmosDataContributorRole.id
    scope: cosmosAccount.id
  }
}

output containerAppName string = containerApp.name
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output cosmosAccountName string = cosmosAccount.name
output managedEnvironmentId string = managedEnvironment.id
