from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class LlmProfile:
    """A resolved (provider, key, model) for one task. `base_url=""` = the SDK default
    (OpenAI)."""

    base_url: str
    api_key: str
    model: str


# Each task can use its own provider/model — a future model that's better at one job (e.g.
# vision extraction, or the cheap judge) can be slotted in without moving the rest. Every
# task falls back to the single global key/endpoint unless explicitly overridden.
LLM_TASKS = ("normalize", "hints", "chat", "judge", "embedding")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Core services
    database_url: str = "postgresql://spotr_user:password@localhost:5432/spotr"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # LLM. Provider-agnostic: any OpenAI-compatible endpoint works by setting
    # `llm_base_url` + `openai_api_key` + model names. Empty base_url = OpenAI itself.
    # Default .env targets Google Gemini's free-tier OpenAI-compatible endpoint.
    # THIS IS THE GLOBAL DEFAULT — every task uses it unless overridden below.
    openai_api_key: str = ""  # the bearer token for whichever provider llm_base_url points at
    llm_base_url: str = ""  # e.g. Gemini: https://generativelanguage.googleapis.com/v1beta/openai/
    llm_model_strong: str = "gpt-5.2"  # default for normalize / hints / chat
    llm_model_fast: str = "gpt-5-mini"  # default for the cheap judge
    embedding_model: str = "text-embedding-3-small"

    # Per-task overrides — ALL optional; blank means "use the global default above". Set
    # only what you want to route elsewhere (e.g. CHAT_BASE_URL + CHAT_API_KEY + CHAT_MODEL
    # to run the tutor on a different provider than normalization). See README "LLM
    # configuration" for which tasks need which provider features.
    normalize_model: str = ""
    normalize_base_url: str = ""
    normalize_api_key: str = ""
    hints_model: str = ""
    hints_base_url: str = ""
    hints_api_key: str = ""
    chat_model: str = ""
    chat_base_url: str = ""
    chat_api_key: str = ""
    judge_model: str = ""
    judge_base_url: str = ""
    judge_api_key: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""

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

    # Ingestion worker (durable Postgres-backed job queue — see app/queue.py).
    # `run_inline_worker` starts the worker inside the API process (dev: one command runs
    # everything). The compose stack sets it false and runs a dedicated `worker` container.
    run_inline_worker: bool = True
    worker_poll_interval_seconds: float = 2.0  # NOTIFY fallback + stuck-recovery cadence
    worker_stuck_timeout_seconds: int = 600  # re-queue a job whose worker died mid-run
    worker_max_attempts: int = 3  # after this many claims, a stuck job is failed, not retried

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

    def llm_profile(self, task: str) -> LlmProfile:
        """Resolve (base_url, api_key, model) for a task: a per-task override if set,
        otherwise the global default. This is what makes one key 'just work' for
        everything while still allowing a different provider per task."""
        if task not in LLM_TASKS:
            raise ValueError(f"unknown LLM task: {task!r}")
        default_model = {
            "normalize": self.llm_model_strong,  # needs vision + structured output
            "hints": self.llm_model_strong,  # needs structured output
            "chat": self.llm_model_strong,  # needs streaming
            "judge": self.llm_model_fast,  # needs structured output (cheap)
            "embedding": self.embedding_model,  # needs an embeddings endpoint
        }[task]
        override = {
            "normalize": (self.normalize_base_url, self.normalize_api_key, self.normalize_model),
            "hints": (self.hints_base_url, self.hints_api_key, self.hints_model),
            "chat": (self.chat_base_url, self.chat_api_key, self.chat_model),
            "judge": (self.judge_base_url, self.judge_api_key, self.judge_model),
            "embedding": (self.embedding_base_url, self.embedding_api_key, ""),
        }[task]
        base_url, api_key, model = override
        return LlmProfile(
            base_url=base_url or self.llm_base_url,
            api_key=api_key or self.openai_api_key,
            model=model or default_model,
        )

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
