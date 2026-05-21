from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM provider: "azure_openai" or "openai"
    # Set to "openai" + OPENAI_API_KEY to use OpenAI directly (no Azure approval needed)
    llm_provider: str = "azure_openai"

    # OpenAI direct (used when llm_provider=openai)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Azure OpenAI (used when llm_provider=azure_openai)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-05-01-preview"

    # Azure AI Search
    azure_search_endpoint: str
    azure_search_api_key: str
    azure_search_code_index: str = "code-index"
    azure_search_docs_index: str = "docs-index"
    # Embedding model — used for vector search.
    # When llm_provider=openai this is the OpenAI model name (e.g. "text-embedding-3-large").
    # When llm_provider=azure_openai this is the Azure deployment name.
    # Set to empty string to disable vector search (keyword-only mode).
    azure_openai_embedding_deployment: str = "text-embedding-3-large"
    azure_search_vector_dimensions: int = 3072   # 3072 for text-embedding-3-large, 1536 for ada-002
    azure_search_top_k: int = 5                  # default results per query

    # Cosmos DB
    cosmos_endpoint: str
    cosmos_key: str
    cosmos_database: str = "agent-db"
    cosmos_sessions_container: str = "sessions"
    cosmos_feedback_container: str = "feedback"
    cosmos_users_container: str = "users"
    cosmos_ingest_container: str = "ingest_registry"

    # Key Vault
    key_vault_url: str = ""

    # Jira
    jira_base_url: str
    jira_api_token: str
    jira_project_key: str
    jira_user_email: str
    # Custom field ID for Acceptance Criteria — varies per Jira instance.
    # Common values: customfield_10016 (Story Points on some), customfield_10024 (AC on Xray),
    # customfield_10006 (Epic Link). Check yours at:
    # GET /rest/api/3/issue/{key}?fields=names
    jira_ac_custom_field: str = "customfield_10016"
    # Story context TTL in seconds — how long to cache fetched story details
    jira_story_cache_ttl: int = 300  # 5 minutes, matches frontend poll interval

    # GitHub (for Copilot extension)
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""

    # Auth (Azure Entra ID)
    entra_tenant_id: str
    entra_client_id: str
    entra_client_secret: str = ""

    # Application Insights
    applicationinsights_connection_string: str = ""

    # Rate limiting (requests per minute per user)
    rate_limit_chat: int = 20
    rate_limit_feedback: int = 60
    rate_limit_jira: int = 30

    # App
    app_environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
