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

    The user provides the problem: `url` identifies it on the chosen `source`, and the
    statement/constraints/samples are supplied directly (paste or screenshot-normalized
    on the client — see step 3). We do NOT scrape restricted problem HTML. Metadata
    (title/rating/tags) and the editorial are fetched only via the source's permitted
    API from `url` / `editorial_url`.
    """

    source: str = "codeforces"
    url: str
    statement_text: str | None = None
    constraints_text: str | None = None
    sample_tests: list[SampleTestIn] | None = None
    editorial_url: str | None = None
    editorial_text: str | None = None


class ProblemOut(BaseModel):
    """Problem metadata + ingestion status. The client polls this after ingest."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    source: str
    external_id: str
    url: str | None
    title: str
    rating: int | None
    tags: list[str]
    statement_text: str
    constraints_text: str | None
    sample_tests: list[dict]
    time_limit_ms: int | None
    memory_limit_mb: int | None
    editorial_url: str | None
    # The raw editorial is deliberately NOT exposed — it would spoil the ladder.
    # Clients get only whether one was captured.
    has_editorial: bool
    is_interactive: bool
    has_multiple_answers: bool
    ingestion_status: str
    ingestion_error: str | None
    created_at: datetime
    # Number of ladder levels generated (0–4). Content is never exposed here — the
    # tutor serves levels one at a time via the revealed-set disclosure flow (step 6).
    level_count: int = 0
