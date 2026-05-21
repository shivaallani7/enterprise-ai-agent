#!/usr/bin/env bash
# Post-provision hook — runs automatically after `azd provision`.
# Reads secrets from azd environment variables and stores them in Key Vault
# so the backend Container App can pull them at startup via KEY_VAULT_URL.
#
# Required azd env vars (set with `azd env set <NAME> <VALUE>`):
#   AZURE_OPENAI_API_KEY
#   AZURE_SEARCH_API_KEY
#   COSMOS_KEY
#   JIRA_API_TOKEN
#   ENTRA_CLIENT_SECRET
#
# Optional:
#   JIRA_AC_CUSTOM_FIELD   (Jira custom field ID for acceptance criteria, e.g. customfield_10014)

set -euo pipefail

# azd exports the Key Vault name as an output from main.bicep
KV_NAME=$(azd env get-value KEY_VAULT_NAME 2>/dev/null || true)

if [[ -z "$KV_NAME" ]]; then
  echo "WARNING: KEY_VAULT_NAME not found in azd env — skipping Key Vault secret population."
  echo "         Run 'azd provision' first, then re-run 'azd up' or this script manually."
  exit 0
fi

echo "Populating Key Vault: $KV_NAME"

set_secret() {
  local name="$1"
  local env_var="$2"
  local value="${!env_var:-}"

  if [[ -z "$value" ]]; then
    echo "  SKIP  $name  (${env_var} not set)"
    return
  fi

  az keyvault secret set \
    --vault-name "$KV_NAME" \
    --name "$name" \
    --value "$value" \
    --output none

  echo "  SET   $name"
}

set_secret "OPENAI-API-KEY"         "OPENAI_API_KEY"
set_secret "AZURE-OPENAI-API-KEY"   "AZURE_OPENAI_API_KEY"
set_secret "AZURE-SEARCH-API-KEY"   "AZURE_SEARCH_API_KEY"
set_secret "COSMOS-KEY"             "COSMOS_KEY"
set_secret "JIRA-API-TOKEN"         "JIRA_API_TOKEN"
set_secret "ENTRA-CLIENT-SECRET"    "ENTRA_CLIENT_SECRET"
set_secret "JIRA-AC-CUSTOM-FIELD"   "JIRA_AC_CUSTOM_FIELD"
set_secret "LANGCHAIN-API-KEY"      "LANGCHAIN_API_KEY"

echo ""
echo "Key Vault secret population complete."
echo ""
echo "After first deploy, run the following to restrict CORS to your frontend:"
echo "  FRONTEND_URL=\$(azd env get-value AZURE_CONTAINER_APP_FRONTEND_URL)"
echo "  azd env set CORS_ORIGINS \"[\\\"https://\$FRONTEND_URL\\\"]\" "
echo "  azd provision"
