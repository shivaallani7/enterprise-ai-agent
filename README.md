# Enterprise AI Agent

A production-ready AI agent system on Azure for engineering teams of ~20. Two products in one repo:

1. **Jira-aware Agent UI** — React app with per-story chat tabs, streaming responses, thumbs-up/down feedback, and a RAGAS quality dashboard.
2. **GitHub Copilot Extension** — VS Code Copilot Chat integration (`@enterprise-ai-agent`) that injects your active branch's Jira story + codebase context.

Both share the same FastAPI backend and the same `OrchestratorAgent` (Semantic Kernel).

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  React Frontend (Azure Container Apps)                  │
│  MSAL auth → story sidebar → streaming chat tabs        │
└────────────────────────┬────────────────────────────────┘
                         │ Bearer JWT (Entra ID)
                         ▼
┌─────────────────────────────────────────────────────────┐
│  Azure API Management (JWT validation, rate limiting)   │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI Backend (Azure Container Apps)                 │
│                                                         │
│  OrchestratorAgent (Semantic Kernel 1.x)                │
│  ├── JiraContextPlugin  → Jira REST API v3              │
│  ├── CodeContextPlugin  → Azure AI Search (code-index)  │
│  └── RAGPlugin          → Azure AI Search (docs-index)  │
│                                                         │
│  POST /api/chat     → SSE token streaming               │
│  POST /api/feedback → SME thumbs up/down + corrections  │
│  POST /api/copilot  → GitHub Copilot extension          │
│  GET  /api/jira/*   → Story metadata + cache control    │
└──────┬────────────────────────────────────┬─────────────┘
       │                                    │
       ▼                                    ▼
  Azure OpenAI (GPT-4o 80K TPM)      Cosmos DB (sessions, feedback)
  text-embedding-3-large              Azure Key Vault (secrets)
  Azure AI Search (semantic)          Application Insights (traces)
  Jira REST API v3
```

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | [python.org](https://python.org) |
| Node | 20+ | [nodejs.org](https://nodejs.org) |
| Azure CLI | latest | `brew install azure-cli` |
| azd | latest | `brew tap azure/azd && brew install azd` |
| Docker Desktop | latest | [docker.com](https://docker.com) |

You also need:
- An Azure subscription with permissions to create resource groups
- A Jira Cloud workspace with an [API token](https://id.atlassian.com/manage-profile/security/api-tokens)
- An Azure Entra ID app registration (see [Entra setup](#entra-id-app-registration))

---

## Local Development

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `backend/.env`:

```env
# Azure OpenAI — get from Azure Portal → OpenAI resource → Keys and Endpoint
AZURE_OPENAI_ENDPOINT=https://your-aoai.openai.azure.com/
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-05-01-preview
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large

# Azure AI Search — get from Azure Portal → Search resource → Keys
AZURE_SEARCH_ENDPOINT=https://your-search.search.windows.net
AZURE_SEARCH_API_KEY=your-key
AZURE_SEARCH_CODE_INDEX=code-index
AZURE_SEARCH_DOCS_INDEX=docs-index
AZURE_SEARCH_VECTOR_DIMENSIONS=3072

# Cosmos DB — get from Azure Portal → Cosmos DB → Keys
COSMOS_ENDPOINT=https://your-cosmos.documents.azure.com:443/
COSMOS_KEY=your-key

# Jira — https://id.atlassian.com/manage-profile/security/api-tokens
JIRA_BASE_URL=https://your-org.atlassian.net
JIRA_API_TOKEN=your-token
JIRA_PROJECT_KEY=PROJ
JIRA_USER_EMAIL=you@example.com

# Entra ID (leave blank in pure dev-token mode below)
ENTRA_TENANT_ID=your-tenant-id
ENTRA_CLIENT_ID=your-client-id

APP_ENVIRONMENT=development   # enables dev-token bypass
LOG_LEVEL=DEBUG
CORS_ORIGINS=["http://localhost:5173"]
```

Start the backend:

```bash
uvicorn app.main:app --reload
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

**Dev-token bypass**: with `APP_ENVIRONMENT=development`, the header `Authorization: Bearer dev-token` bypasses JWT validation. The frontend sends this automatically in dev mode.

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
```

Fill in `frontend/.env.local`:

```env
VITE_ENTRA_CLIENT_ID=your-client-id    # same as backend ENTRA_CLIENT_ID
VITE_ENTRA_TENANT_ID=your-tenant-id    # same as backend ENTRA_TENANT_ID
```

Start the frontend:

```bash
npm run dev
# → http://localhost:5173
```

The Vite dev server proxies `/api/` to `http://localhost:8000` automatically (configured in `vite.config.ts`). MSAL auth is skipped in `dev-token` mode — you'll be automatically signed in as `Dev User`.

### 3. Docker Compose (alternative)

Builds and runs both services together. Useful for integration testing against the real nginx proxy.

```bash
cp backend/.env.example backend/.env
# Fill in backend/.env values

docker compose up --build
# Frontend: http://localhost:5173
# Backend:  http://localhost:8000
```

---

## Search Index Setup

After the backend is running (locally or in Azure), create the AI Search indexes and seed them:

```bash
cd backend

# 1. Create index schemas (safe to re-run)
python -m scripts.setup_indexes

# 2. Index your codebase
python -m scripts.index_code --repo /path/to/your/repo

# 3. Index documentation
python -m scripts.index_docs --docs /path/to/docs

# Dry-run first to preview chunks without uploading:
python -m scripts.index_code --repo /path/to/repo --dry-run
python -m scripts.index_docs --docs /path/to/docs --dry-run
```

---

## Deploy to Azure

### Entra ID App Registration

Before deploying, create an app registration in Azure Entra ID:

1. **Azure Portal → Entra ID → App registrations → New registration**
   - Name: `Enterprise AI Agent`
   - Supported account types: _Accounts in this organizational directory only_
   - Redirect URI: leave blank for now
2. After creation, note the **Application (client) ID** and **Directory (tenant) ID**
3. **Certificates & secrets → New client secret** — note the value
4. **Expose an API → Add a scope**:
   - Scope name: `access_as_user`
   - Who can consent: Admins and users
5. **Authentication → Add a platform → Single-page application**
   - Add `http://localhost:5173` for local dev
   - After first Azure deploy, add `https://<frontend-fqdn>` too

### First Deploy

```bash
# 1. Authenticate
azd auth login
az login

# 2. Initialise the azd environment
azd init --environment production

# 3. Set required configuration (non-secret)
azd env set JIRA_BASE_URL       "https://your-org.atlassian.net"
azd env set JIRA_PROJECT_KEY    "PROJ"
azd env set JIRA_USER_EMAIL     "you@example.com"
azd env set ENTRA_CLIENT_ID     "your-client-id"
azd env set PUBLISHER_EMAIL     "admin@your-org.com"
azd env set CORS_ORIGINS        '["*"]'     # tightened after first deploy

# 4. Set secrets (stored in Key Vault by the postprovision hook)
azd env set AZURE_OPENAI_API_KEY   "your-key"
azd env set AZURE_SEARCH_API_KEY   "your-key"
azd env set COSMOS_KEY             "your-key"
azd env set JIRA_API_TOKEN         "your-token"
azd env set ENTRA_CLIENT_SECRET    "your-secret"

# Optional — Jira custom field ID for acceptance criteria (default: customfield_10014)
azd env set JIRA_AC_CUSTOM_FIELD   "customfield_10016"

# 5. Provision infrastructure + deploy containers
azd up
```

`azd up` runs in this order:
1. `azd provision` — creates all Azure resources via Bicep
2. `postprovision` hook — populates Key Vault with secrets
3. `azd deploy` — builds Docker images, pushes to ACR, updates Container Apps

### After First Deploy

```bash
# Get the frontend URL from azd outputs
FRONTEND_URL=$(azd env get-value AZURE_CONTAINER_APP_FRONTEND_URL)

# Tighten CORS to the actual frontend origin
azd env set CORS_ORIGINS "[\"https://${FRONTEND_URL}\"]"
azd provision   # re-provisions only the ACA resource (fast)

# Add the frontend URL to Entra ID redirect URIs
# Azure Portal → Entra ID → App registrations → your app →
#   Authentication → Add URI: https://<FRONTEND_URL>

# Set up search indexes against the deployed backend
cd backend
AZURE_OPENAI_ENDPOINT=... AZURE_SEARCH_ENDPOINT=... \
  python -m scripts.setup_indexes
```

### Re-deploying After Code Changes

```bash
azd deploy           # rebuilds + pushes both images, no re-provision
azd deploy backend   # backend only
azd deploy frontend  # frontend only
```

---

## GitHub Copilot Extension Registration

The Copilot extension is a GitHub App that calls `POST /api/copilot` on your backend.

1. **GitHub Settings → Developer Settings → GitHub Apps → New GitHub App**
   - Name: `Enterprise AI Agent`
   - Homepage URL: `https://<your-backend-fqdn>`
   - Webhook: disable (not needed)
   - **Copilot** (expand section):
     - Agent URL: `https://<your-backend-fqdn>/api/copilot`
   - **Permissions**:
     - Repository → Contents: Read-only
     - Repository → Pull requests: Read-only
     - Repository → Issues: Read-only
   - Uncheck _Expire user authorization tokens_

2. After creation, note the **App ID**. Under **Private keys**, generate and download a key (not used by this agent, but required by GitHub Apps).

3. **Install the app** on your organisation or specific repositories.

4. Update `copilot-extension.json` with your actual backend URL, then commit and push.

5. **Using the extension** in VS Code:
   - Open GitHub Copilot Chat
   - Type `@enterprise-ai-agent what does PROJ-123 require for the auth flow?`
   - The agent auto-detects `PROJ-123` from your current branch name if it matches the pattern `feature/PROJ-123-*`, `fix/PROJ-123-*`, or `chore/PROJ-123-*`

---

## RAGAS Evaluation

The RAGAS pipeline runs automatically on every PR that touches `backend/`. It loads golden Q&A pairs from Cosmos DB, calls the live agent for answers, and scores four metrics.

**Thresholds** (`backend/ragas_eval/thresholds.yaml`):

| Metric | Threshold | Meaning |
|--------|-----------|---------|
| `faithfulness` | ≥ 0.85 | Answer is grounded in retrieved context |
| `answer_relevancy` | ≥ 0.80 | Answer directly addresses the question |
| `context_recall` | ≥ 0.75 | Retrieved context covers the ground truth |
| `context_precision` | ≥ 0.75 | Retrieved context has low noise |

**Seeding the golden dataset** (required before first RAGAS run):

```bash
cd backend

# Option A: Generate synthetic Q&A from Jira stories via GPT-4o
python -m ragas_eval.dataset_builder

# Option B: Golden data accumulates automatically — any thumbs-down response
# in the UI where the user provides a correction becomes a golden pair.
# The weekly feedback-processor.yml workflow promotes these automatically.
```

**Running locally**:

```bash
cd backend
# Requires a running backend and access to Cosmos DB
EVAL_API_BASE=http://localhost:8000 \
EVAL_API_TOKEN=dev-token \
python -m ragas_eval.eval_pipeline
```

---

## GitHub Actions — Required Secrets

Set these in **GitHub → Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `AZURE_CLIENT_ID` | Service principal client ID for OIDC deploy |
| `AZURE_TENANT_ID` | Azure tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint URL |
| `AZURE_SEARCH_API_KEY` | Azure AI Search API key |
| `COSMOS_ENDPOINT` | Cosmos DB endpoint URL |
| `COSMOS_KEY` | Cosmos DB primary key |
| `JIRA_BASE_URL` | Jira base URL |
| `JIRA_API_TOKEN` | Jira API token |
| `JIRA_PROJECT_KEY` | Jira project key |
| `JIRA_USER_EMAIL` | Jira user email |
| `ENTRA_TENANT_ID` | Entra tenant ID |
| `ENTRA_CLIENT_ID` | Entra app client ID |

Set these as **Variables** (non-secret):

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o` | Chat model deployment name |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | `text-embedding-3-large` | Embedding deployment name |
| `AZD_ENV_NAME` | `production` | azd environment name for deploy workflow |

**Creating the OIDC service principal** for the deploy workflow:

```bash
az ad sp create-for-rbac \
  --name "enterprise-ai-agent-deploy" \
  --role Contributor \
  --scopes /subscriptions/<subscription-id> \
  --sdk-auth
```

Then add the `clientId`, `tenantId`, and `subscriptionId` as the three GitHub secrets above.

---

## Project Structure

```
/
├── infra/                         # Bicep IaC
│   ├── main.bicep                 # Root module
│   ├── main.parameters.json       # azd parameter mapping
│   └── modules/
│       ├── containerapps.bicep    # ACA environment + backend + frontend
│       ├── openai.bicep           # GPT-4o + text-embedding-3-large
│       ├── search.bicep           # Azure AI Search (Standard + semantic)
│       ├── cosmos.bicep           # Cosmos DB serverless (sessions + feedback)
│       ├── keyvault.bicep         # Key Vault (RBAC mode)
│       ├── appinsights.bicep      # App Insights + Log Analytics
│       ├── acr.bicep              # Azure Container Registry
│       └── apim.bicep             # API Management (Consumption tier)
│
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   ├── orchestrator.py    # ChatCompletionAgent + invoke_stream()
│   │   │   ├── jira_context_agent.py
│   │   │   ├── code_context_agent.py
│   │   │   ├── rag_agent.py
│   │   │   ├── prompts.py
│   │   │   ├── retry.py
│   │   │   └── source_tracker.py
│   │   ├── api/
│   │   │   ├── chat.py            # POST /api/chat (SSE)
│   │   │   ├── feedback.py        # POST /api/feedback
│   │   │   ├── copilot.py         # POST /api/copilot
│   │   │   ├── jira.py            # GET /api/jira/stories
│   │   │   └── deps.py            # JWT auth + Cosmos singletons
│   │   ├── integrations/
│   │   │   ├── jira_client.py     # Jira REST API v3 + TTL cache
│   │   │   ├── search_client.py   # Hybrid search + IndexManager
│   │   │   └── indexer.py         # Batch upload + embedding
│   │   ├── memory/
│   │   │   └── cosmos_store.py    # SessionStore + FeedbackStore
│   │   ├── middleware/
│   │   │   ├── logging.py
│   │   │   └── security.py
│   │   ├── config.py
│   │   ├── limiter.py
│   │   └── main.py
│   ├── ragas_eval/
│   │   ├── eval_pipeline.py       # Load golden → call agent → RAGAS score
│   │   ├── dataset_builder.py     # Synthetic Q&A via GPT-4o
│   │   ├── feedback_processor.py  # Weekly: promote corrections to golden set
│   │   └── thresholds.yaml
│   ├── scripts/
│   │   ├── setup_indexes.py       # Create AI Search index schemas
│   │   ├── index_code.py          # Chunk + upload source code
│   │   └── index_docs.py          # Chunk + upload documentation
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .env.example
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx     # Streaming chat + markdown renderer
│   │   │   ├── StorySidebar.tsx   # Story list + 5-min poll
│   │   │   ├── StoryTab.tsx       # Story header + collapsible details
│   │   │   ├── FeedbackWidget.tsx # Thumbs up/down + correction input
│   │   │   └── Dashboard.tsx      # RAGAS trend charts (Recharts)
│   │   ├── hooks/
│   │   │   ├── useSSE.ts          # SSE streaming + stale-closure fix
│   │   │   └── useJiraStories.ts  # Story list with 5-min poll
│   │   └── lib/
│   │       ├── api.ts             # API client with auth token injection
│   │       └── authConfig.ts      # MSAL config
│   ├── Dockerfile                 # Multi-stage: Node build → nginx serve
│   ├── .dockerignore
│   ├── nginx.conf                 # nginx template (envsubst NGINX_BACKEND_URL)
│   ├── docker-entrypoint.sh
│   └── .env.example
│
├── scripts/
│   ├── postprovision.sh           # azd hook: populate Key Vault secrets
│   └── postprovision.ps1          # Windows equivalent
│
├── .github/workflows/
│   ├── ci.yml                     # Ruff + mypy + tsc + eslint on PRs
│   ├── ragas-eval.yml             # RAGAS metrics gate on PRs touching backend/
│   ├── feedback-processor.yml     # Weekly: promote feedback to golden dataset
│   └── deploy.yml                 # azd up on merge to main
│
├── copilot-extension.json         # GitHub App manifest
├── docker-compose.yml             # Local integration testing
└── azure.yaml                     # azd root config + postprovision hook
```

---

## Key Design Decisions

**Why Semantic Kernel `invoke_stream()` and not function calling with a planner?**
`FunctionCallingStepwisePlanner` completes the full plan before returning any tokens, so the user sees nothing for several seconds. `ChatCompletionAgent` with `FunctionChoiceBehavior.Auto` streams real tokens from the first GPT response while SK auto-invokes tools transparently in the background.

**Why a messagesRef in useChat?**
`useCallback` with `[sessionId, storyId]` as deps means `sendMessage` is only recreated when the story changes. Without `messagesRef`, the callback closes over stale `state.messages` — sending a second message before the first stream finishes would silently drop the in-flight assistant reply from the history.

**Why `<<<TOKEN>>>` substitution in prompts?**
Jira story descriptions often contain code snippets with `{variable}` braces. `str.format(story=story_text)` raises `KeyError` on the first `{anything}`. Using `str.replace("<<<STORY>>>", story_text)` sidesteps this completely.

**Why APIM Consumption tier?**
APIM Standard adds ~45 minutes to provision time and costs ~$700/month. Consumption tier provisions in under 2 minutes, costs per-call, and provides the same JWT validation policy. For a team of 20, throughput is not the constraint.
