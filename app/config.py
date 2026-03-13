from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "policy-chatbot"
    app_env: str = "dev"
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000

    allow_anonymous: bool = True
    cors_allowed_origins: str = "*"

    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_audience: str = ""

    foundry_project_endpoint: str = ""
    foundry_project_name: str = ""
    foundry_resource_name: str = ""
    foundry_workflow_name: str = ""
    foundry_workflow_id: str = ""
    foundry_workflow_endpoint: str = ""
    foundry_workflow_api_version: str = "2025-11-15-preview"
    foundry_workflow_base_endpoint: str = ""
    foundry_workflow_run_api_version: str = ""
    foundry_workflow_scope: str = "https://ml.azure.com/.default"
    foundry_subscription_id: str = ""
    foundry_resource_group: str = ""
    foundry_workspace_name: str = ""
    foundry_scope: str = "https://ai.azure.com/.default"
    foundry_api_key: Optional[str] = None
    foundry_timeout_seconds: int = 30

    cosmos_enabled: bool = False
    cosmos_endpoint: str = ""
    cosmos_database: str = "policy-chatbot-db"
    cosmos_sessions_container: str = "chat-sessions"
    cosmos_messages_container: str = "chat-messages"
    cosmos_key: Optional[str] = None

    appinsights_connection_string: Optional[str] = None

    @property
    def policy_files(self) -> dict[str, Path]:
        root = Path(__file__).resolve().parents[1]
        return {
            "card_services": root / "data" / "card-services-policies.md",
            "fraud": root / "data" / "fraud-policies.md",
            "refunds_disputes": root / "data" / "refunds-and-disputes-policies.md",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
