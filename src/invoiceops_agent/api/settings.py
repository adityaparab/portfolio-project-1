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

    # Dependency endpoints (service names inside Compose, localhost in dev).
    # database_dsn runs as the least-privilege app role; alembic_dsn as the
    # schema owner (migrations only — issue #7).
    database_dsn: str = "postgresql://invoiceops:invoiceops@localhost:5432/invoiceops"
    alembic_dsn: str | None = None  # falls back to database_dsn
    minio_base_url: str = "http://localhost:9000"
    minio_access_key: str = "invoiceops"
    minio_secret_key: str = "invoiceops-secret"
    minio_bucket: str = "raw-invoices"
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = "sk-local-dev"

    # Gateway client (issue #15)
    gateway_timeout_seconds: float = 120.0
    gateway_infra_retries: int = 2
    gateway_token_budgets: dict[str, int] = {
        "extract-vision": 16_384,
        "triage-reasoner": 8_192,
        "eval-judge": 8_192,
        "embed": 2_048,
    }
    gateway_redaction_enabled: bool = True
    # Escape hatch for proxies that do not define our virtual aliases (e.g. the
    # native dev proxy): maps alias -> backend model name sent on the wire.
    # Preferred setup: define the aliases in the proxy itself (deploy/litellm).
    gateway_model_map_json: str = "{}"

    log_level: str = "INFO"

    # Frontend dev server (Vite proxy origin)
    cors_origins: list[str] = ["http://localhost:5173"]

    service_token: str = "dev-service-token"

    # Email webhook (issue #12)
    email_webhook_secret: str = "dev-webhook-secret"
    email_webhook_freshness_seconds: int = 300

    # Ingestion limits (issue #11)
    max_upload_bytes: int = 20 * 1024 * 1024  # 20 MiB
    allowed_content_types: list[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
    ]

    # Graph node retries (issue #27): INFRA-only, exponential backoff + jitter
    graph_retry_attempts: int = 3
    graph_retry_base_delay_seconds: float = 0.5
    graph_retry_max_delay_seconds: float = 8.0
    graph_retry_jitter_seconds: float = 0.25
