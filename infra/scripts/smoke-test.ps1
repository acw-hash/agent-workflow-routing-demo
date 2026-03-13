param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl
)

$ErrorActionPreference = "Stop"

Write-Host "Checking health endpoint..."
$health = Invoke-RestMethod -Uri "$BaseUrl/health" -Method Get
if ($health.status -ne "ok") {
    throw "Health endpoint did not return status=ok"
}

Write-Host "Creating anonymous session for smoke test..."
$session = Invoke-RestMethod -Uri "$BaseUrl/api/chat/session" -Method Post -ContentType "application/json" -Body "{}"

if (-not $session.session_id) {
    throw "Session creation failed"
}

Write-Host "Sending test message..."
$chatBody = @{
    session_id = $session.session_id
    message    = "My card was declined and I need help"
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "$BaseUrl/api/chat/message" -Method Post -ContentType "application/json" -Body $chatBody
if (-not $response.assistant_response) {
    throw "Chat endpoint did not return assistant response"
}

Write-Host "Smoke test passed."
Write-Host "Response domain: $($response.domain)"
