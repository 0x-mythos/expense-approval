"""Application settings, loaded from environment variables (.env supported)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Signs the session cookie. Override in production.
    secret_key: str = "dev-secret-change-me"

    # SQLAlchemy URL. Defaults to the docker-compose Postgres service.
    database_url: str = "postgresql+psycopg2://expense:expense@db:5432/expense"

    # AI helper (optional). Empty key => the app runs with AI gracefully disabled.
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5"
    ai_timeout_seconds: float = 12.0

    # Safety budget: hard caps on how many AI calls can run, so a bug or a hung
    # loop can never burn through tokens. Beyond these, AI degrades to "unavailable".
    ai_max_calls_per_day: int = 300
    ai_max_calls_per_min: int = 20

    # When True, on submit the AI may auto-correct an obviously-wrong category
    # (only if it confidently flags a mismatch). Set False to only suggest.
    ai_auto_categorize: bool = True

    # Where uploaded receipts are stored (mount a volume in production).
    upload_dir: str = "uploads"
    max_receipt_mb: int = 10


settings = Settings()
