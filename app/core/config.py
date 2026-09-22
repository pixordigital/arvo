"""ARVO — Settings. PRINCÍPIO VERDADE: limites reais só de PLANS + limits.py."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ARVO_", extra="ignore")

    app_name: str = "ARVO"
    debug: bool = False
    app_url: str = "http://178.105.181.38:9777"

    # DB
    database_url: str = "sqlite+aiosqlite:///./arvo.db"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    supabase_studio_url: str = "http://178.105.181.38:9771"

    # Redis / Workers
    redis_url: str = "redis://redis:6379/0"

    # LLM — OpenRouter default per user choice
    llm_provider: str = "openrouter"  # openrouter | openai | anthropic | gemini
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # Security
    jwt_secret: str = "change-me-generate-openssl-rand-hex-32"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    https_only: bool = False
    cors_origins: str = ""
    rate_limit_per_minute: int = 60

    # CRM integrations
    hubspot_api_key: str = ""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    pipedrive_api_token: str = ""
    twenty_crm_url: str = ""
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    outlook_client_id: str = ""
    outlook_client_secret: str = ""
    # ARVO ↔ AIOS integration (Fase 1A — feature-flag off por padrão)
    aios_integration_enabled: bool = False
    aios_base_url: str = ""
    aios_service_key_id: str = ""
    aios_service_key: str = ""


settings = Settings()

# PLANS — fonte verdade para home/pricing/docs (princípio inegociável 2026-09-13)
PLANS: dict[str, dict] = {
    "starter": {"max_orgs": 1, "max_users": 5, "max_accounts": 100, "max_findings": 50},
    "pro": {"max_orgs": 3, "max_users": 25, "max_accounts": 1000, "max_findings": 500},
    "enterprise": {"max_orgs": 10, "max_users": 100, "max_accounts": 10000, "max_findings": 5000},
}
