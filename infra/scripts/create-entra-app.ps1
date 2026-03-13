param(
    [Parameter(Mandatory = $false)]
    [string]$SubscriptionId = "",

    [Parameter(Mandatory = $false)]
    [string]$AppDisplayName = "policy-chatbot-dev",

    [Parameter(Mandatory = $false)]
    [string]$ParametersFile = "infra/bicep/parameters.dev.json",

    [Parameter(Mandatory = $false)]
    [string]$RedirectUri = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

if (-not $SubscriptionId) {
    Write-Host "Resolving active subscription from Azure CLI context"
    $SubscriptionId = az account show --query id -o tsv
    if (-not $SubscriptionId) {
        throw "Unable to resolve subscription ID from current Azure account context. Pass -SubscriptionId explicitly."
    }
}

Write-Host "Setting active subscription $SubscriptionId"
az account set --subscription $SubscriptionId

Write-Host "Resolving tenant context"
$tenantId = az account show --query tenantId -o tsv
if (-not $tenantId) {
    throw "Unable to resolve tenant ID from current Azure account context."
}

Write-Host "Checking for existing app registration named $AppDisplayName"
$existingAppId = az ad app list --display-name $AppDisplayName --query "[0].appId" -o tsv

if ($existingAppId) {
    Write-Host "Using existing app registration: $existingAppId"
    $appId = $existingAppId
}
else {
    Write-Host "Creating new app registration"
    $appId = az ad app create --display-name $AppDisplayName --sign-in-audience AzureADMyOrg --query appId -o tsv
    if (-not $appId) {
        throw "Failed to create Entra app registration."
    }
    Write-Host "Created app registration: $appId"
}

Write-Host "Ensuring service principal exists"
$spId = az ad sp list --filter "appId eq '$appId'" --query "[0].id" -o tsv
if (-not $spId) {
    az ad sp create --id $appId | Out-Null
}

$appIdUri = "api://$appId"
Write-Host "Updating identifier URI to $appIdUri"
az ad app update --id $appId --identifier-uris $appIdUri | Out-Null

Write-Host "Adding SPA redirect URI $RedirectUri"
$webUris = az ad app show --id $appId --query "web.redirectUris" -o json | ConvertFrom-Json
if (-not $webUris) {
    $webUris = @()
}
if ($webUris -notcontains $RedirectUri) {
    $webUris += $RedirectUri
    az ad app update --id $appId --web-redirect-uris $webUris | Out-Null
}

if (-not (Test-Path $ParametersFile)) {
    throw "Parameters file not found: $ParametersFile"
}

Write-Host "Updating $ParametersFile with tenant/client/audience values"
$parameters = Get-Content $ParametersFile -Raw | ConvertFrom-Json
$parameters.parameters.entraTenantId.value = $tenantId
$parameters.parameters.entraClientId.value = $appId
$parameters.parameters.entraAudience.value = $appId
$parameters | ConvertTo-Json -Depth 100 | Set-Content $ParametersFile

Write-Host "Entra app setup complete"
Write-Host "tenantId: $tenantId"
Write-Host "clientId: $appId"
Write-Host "audience: $appId"
Write-Host "Next: add your deployed container app URL as SPA redirect URI after first deployment."
