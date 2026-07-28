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

    # Grounding (RAG over the openly-licensed corpora). The Qdrant collection name embeds
    # the embedding model: changing the model means a different vector space, so it must be
    # a different collection (re-seed), never a silent mix. `embedding_dim` must match the
    # model's output (gemini-embedding-001 → 3072; text-embedding-3-small → 1536).
    grounding_collection_prefix: str = "grounding"
    embedding_dim: int = 3072
    embedding_batch_size: int = 32  # texts per embeddings API call (free-tier friendly)
    # Where `python -m app.ingestion.grounding` clones/caches the source corpora locally.
    corpora_dir: str = "./corpora"
    grounding_top_k: int = 4  # chunks retrieved per tutor turn

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
    def grounding_collection(self) -> str:
        """Qdrant collection name, scoped to the embedding model so a model change can't
        silently query vectors from an incompatible space."""
        safe_model = self.embedding_model.replace("/", "_")
        return f"{self.grounding_collection_prefix}__{safe_model}"

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
