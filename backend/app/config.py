from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core services
    database_url: str = "postgresql://forcesai_user:password@localhost:5432/forcesai"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # LLM. Provider-agnostic: any OpenAI-compatible endpoint works by setting
    # `llm_base_url` + `openai_api_key` + model names. Empty base_url = OpenAI itself.
    # Default .env targets Google Gemini's free-tier OpenAI-compatible endpoint.
    openai_api_key: str = ""  # the bearer token for whichever provider llm_base_url points at
    llm_base_url: str = ""  # e.g. Gemini: https://generativelanguage.googleapis.com/v1beta/openai/
    llm_model_strong: str = "gpt-5.2"
    llm_model_fast: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Auth
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Frontend deploys to its own origin, so CORS is required, not optional.
    # Comma-separated; the browser sends credentials, so "*" is not usable here.
    cors_origins: str = "http://localhost:5173"

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
