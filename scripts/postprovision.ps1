# Post-provision hook (Windows/PowerShell equivalent of postprovision.sh)
$ErrorActionPreference = "Stop"

$kvName = azd env get-value KEY_VAULT_NAME 2>$null
if (-not $kvName) {
    Write-Warning "KEY_VAULT_NAME not found in azd env — skipping Key Vault population."
    exit 0
}

Write-Host "Populating Key Vault: $kvName"

function Set-KvSecret {
    param([string]$SecretName, [string]$EnvVar)
    $value = [System.Environment]::GetEnvironmentVariable($EnvVar)
    if (-not $value) {
        Write-Host "  SKIP  $SecretName  ($EnvVar not set)"
        return
    }
    az keyvault secret set --vault-name $kvName --name $SecretName --value $value --output none | Out-Null
    Write-Host "  SET   $SecretName"
}

Set-KvSecret "AZURE-OPENAI-API-KEY"  "AZURE_OPENAI_API_KEY"
Set-KvSecret "AZURE-SEARCH-API-KEY"  "AZURE_SEARCH_API_KEY"
Set-KvSecret "COSMOS-KEY"            "COSMOS_KEY"
Set-KvSecret "JIRA-API-TOKEN"        "JIRA_API_TOKEN"
Set-KvSecret "ENTRA-CLIENT-SECRET"   "ENTRA_CLIENT_SECRET"
Set-KvSecret "JIRA-AC-CUSTOM-FIELD"  "JIRA_AC_CUSTOM_FIELD"

Write-Host "`nKey Vault secret population complete."
