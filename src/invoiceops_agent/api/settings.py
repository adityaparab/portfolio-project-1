"""Application settings (12-factor): env-prefixed, overridable in tests."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the InvoiceOps API service."""

    model_config = SettingsConfigDict(
        env_prefix="INVOICEOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Dependency endpoints (service names inside Compose, localhost in dev)
    database_dsn: str = "postgresql://invoiceops:invoiceops@localhost:5432/invoiceops"
    minio_base_url: str = "http://localhost:9000"
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = "sk-local-dev"

    log_level: str = "INFO"

    # Frontend dev server (Vite proxy origin)
    cors_origins: list[str] = ["http://localhost:5173"]

    service_token: str = "dev-service-token"
