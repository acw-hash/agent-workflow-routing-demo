param(
    [Parameter(Mandatory = $false)]
    [string]$SubscriptionId = "",

    [Parameter(Mandatory = $false)]
    [string]$ResourceGroupName = "rg-policy-chatbot-dev",

    [Parameter(Mandatory = $false)]
    [string]$Location = "swedencentral",

    [Parameter(Mandatory = $false)]
    [string]$TemplateFile = "infra/bicep/main.bicep",

    [Parameter(Mandatory = $false)]
    [string]$ParametersFile = "infra/bicep/parameters.dev.json",

    [Parameter(Mandatory = $false)]
    [string]$ImageTag = "dev-$(Get-Date -Format yyyyMMddHHmmss)"
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

Write-Host "Creating or updating resource group $ResourceGroupName in $Location"
az group create --name $ResourceGroupName --location $Location | Out-Null

$params = Get-Content $ParametersFile -Raw | ConvertFrom-Json
$prefix = $params.parameters.prefix.value
$environmentName = $params.parameters.environmentName.value

if (-not $prefix -or -not $environmentName) {
    throw "parameters.dev.json must include prefix and environmentName values."
}

$acrName = (($prefix + $environmentName + "acr") -replace "-", "").ToLower()
$acrLoginServer = "$acrName.azurecr.io"

Write-Host "Ensuring ACR $acrName exists"
$acrExists = az acr show --name $acrName --resource-group $ResourceGroupName --query name -o tsv 2>$null
if (-not $acrExists) {
    az acr create --name $acrName --resource-group $ResourceGroupName --location $Location --sku Basic --admin-enabled false | Out-Null
}

$image = "$acrLoginServer/policy-chatbot:$ImageTag"
Write-Host "Building and pushing image $image"
az acr build --registry $acrName --image "policy-chatbot:$ImageTag" .

Write-Host "Deploying infrastructure and app"
$deploymentName = "policy-chatbot-dev-$(Get-Date -Format yyyyMMddHHmmss)"
az deployment group create `
    --resource-group $ResourceGroupName `
    --name $deploymentName `
    --template-file $TemplateFile `
    --parameters "@$ParametersFile" `
    --parameters containerImage="$image" | Out-Null

$containerAppUrl = az deployment group show `
    --resource-group $ResourceGroupName `
    --name $deploymentName `
    --query "properties.outputs.containerAppUrl.value" `
    --output tsv

Write-Host "Deployment complete."
Write-Host "Chatbot URL: $containerAppUrl"
