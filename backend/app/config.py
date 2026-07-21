from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core services
    database_url: str = "postgresql://forcesai_user:password@localhost:5432/forcesai"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # Code execution sandbox
    judge0_url: str = "http://localhost:2358"
    judge0_api_key: str = ""

    # LLM
    openai_api_key: str = ""
    llm_model_strong: str = "gpt-5.2"
    llm_model_fast: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Auth
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Frontend deploys to its own origin, so CORS is required, not optional.
    # Comma-separated; the browser sends credentials, so "*" is not usable here.
    cors_origins: str = "http://localhost:5173"

    # Quotas
    stress_test_max_cases: int = 30
    stress_test_daily_limit_per_user: int = 10

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sqlalchemy_database_url(self) -> str:
        """SQLAlchemy async URL using the psycopg3 driver.

        Accepts a plain postgresql:// URL in .env (what hosting providers hand out)
        and upgrades it to the explicit async driver form.
        """
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
