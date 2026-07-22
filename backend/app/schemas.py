from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    cf_handle: str | None
    created_at: datetime


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class SampleTestIn(BaseModel):
    input: str
    expected_output: str


class ProblemIngest(BaseModel):
    """Request body for `POST /problems/ingest`.

    Manual paste is the reliable path, not a fallback hack: Codeforces sits behind
    Cloudflare and returns 403 to server-side scrapers, so pasting `statement_text`
    and `sample_tests` (and optionally the editorial) is how a problem gets fully
    ingested. When they're supplied, scraping is skipped entirely. When they're not,
    the pipeline attempts a best-effort scrape. Metadata (title/rating/tags) always
    comes from the CF API, which is not Cloudflare-protected. `editorial_url` is an
    optional hint for future auto-discovery.
    """

    cf_url: str
    statement_text: str | None = None
    constraints_text: str | None = None
    sample_tests: list[SampleTestIn] | None = None
    editorial_url: str | None = None
    editorial_text: str | None = None


class ProblemOut(BaseModel):
    """Problem metadata + ingestion status. The client polls this after ingest."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cf_url: str
    cf_contest_id: int
    cf_index: str
    title: str
    rating: int | None
    tags: list[str]
    statement_text: str
    constraints_text: str | None
    sample_tests: list[dict]
    time_limit_ms: int | None
    memory_limit_mb: int | None
    editorial_url: str | None
    # The raw editorial is deliberately NOT exposed — it would spoil the graduated
    # hints. Clients get only whether one was captured.
    has_editorial: bool
    is_interactive: bool
    has_multiple_answers: bool
    ingestion_status: str
    ingestion_error: str | None
    ingested_at: datetime
    # Number of graduated hints generated (0–4). Content is never exposed here — the
    # reveal flow (step 4) serves levels one at a time through the hint session.
    hint_count: int = 0
