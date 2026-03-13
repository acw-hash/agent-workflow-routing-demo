# Agent Workflow Routing Demo

A FastAPI chatbot that routes bank-policy questions (fraud, refunds/disputes, card services) to an Azure AI Foundry workflow and persists chat history in Azure Cosmos DB.

## What This Implements

- Web chat UI served from the same container app
- Backend API with Microsoft Entra token validation support
- Policy-domain routing with guardrail-aware fallbacks
- Azure AI Foundry workflow invocation (managed identity or API key)
- Cosmos DB-backed chat history (sessions + messages)
- Application Insights-ready configuration
- Azure Container Apps deployment via Bicep + PowerShell

## Project Structure

- `app/` FastAPI app, policy router, auth, and service integrations
- `data/` policy markdown sources used for routing context
- `infra/bicep/` Azure infrastructure template + dev parameter file
- `infra/scripts/` deployment, smoke test, and local run scripts
- `tests/` unit tests

## Local Run

1. Copy `.env.example` to `.env`.
2. Leave `ALLOW_ANONYMOUS=true` for local smoke testing.
3. Run:

```powershell
./infra/scripts/run-local.ps1
```

4. Open `http://localhost:8000`.

## Azure Deployment (Dev, Swedencentral)

1. Copy `infra/bicep/parameters.dev.example.json` to `infra/bicep/parameters.dev.json`.
2. Fill `infra/bicep/parameters.dev.json` with real values:
  - `foundryProjectEndpoint`, `foundryProjectName`, `foundryResourceName`, `foundryWorkflowName`
  - optional explicit override: `foundryWorkflowEndpoint`
  - optional `foundryApiKey` (leave empty for managed identity)
3. Ensure you are logged in: `az login`
4. Create or update Entra app registration and auto-write auth values:

```powershell
./infra/scripts/create-entra-app.ps1 -SubscriptionId <subscription-id> -AppDisplayName policy-chatbot-dev -ParametersFile infra/bicep/parameters.dev.json -RedirectUri http://localhost:8000
```

5. Run deployment:

```powershell
./infra/scripts/deploy-dev.ps1 -SubscriptionId <subscription-id> -ResourceGroupName <resource-group-name> -Location swedencentral
```

6. Run smoke test (only works if anonymous mode is enabled in deployed settings):

```powershell
./infra/scripts/smoke-test.ps1 -BaseUrl https://<your-container-app-fqdn>
```

## Notes On Identity and RBAC

- Container App uses system-assigned managed identity.
- Bicep assigns:
  - `AcrPull` for image pulls from ACR
  - Cosmos DB built-in data contributor role for data plane access
- If your Foundry workflow requires explicit RBAC, grant the app identity access to your Foundry project/endpoint.
- The backend attempts workflow execution using Foundry workflow run APIs first (thread + run). If that fails, it preserves failover behavior: direct workflow endpoint patterns are attempted, and then assistant bridge executes `routing-agent` plus the selected domain agent (`fraud-agent`, `refunds-agent`, or `card-services-agent`).
- For workflow-run execution, you can set `FOUNDRY_WORKFLOW_ID` to the deployed workflow ID (recommended when multiple similarly named deployments exist). If omitted, `FOUNDRY_WORKFLOW_NAME` is used.

## Test

```powershell
python -m pytest -q
```
