from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ledger"
    test_database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ledger_test"
    log_level: str = "INFO"
    # Fail fast instead of queueing forever behind a held row lock.
    db_lock_timeout_ms: int = 5000
    app_name: str = "Transaction Ledger API"
    api_prefix: str = "/api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
