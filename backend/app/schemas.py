from datetime import datetime
from typing import Literal

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


class NormalizeRequest(BaseModel):
    """Raw problem input to clean: pasted `text` and/or one or more screenshot `images`
    (each a `data:` URI, base64). At least one of `text`/`images` must be provided.
    Multiple images let a long problem be captured across several screenshots."""

    text: str | None = None
    images: list[str] | None = None


class Figure(BaseModel):
    """A user-uploaded diagram, tagged with the section it belongs to. `data_uri` is a
    base64 `data:` URI. The AI can't reproduce figures as text, so the user supplies them."""

    data_uri: str
    section: Literal["statement", "note"] = "statement"


class NormalizedOut(BaseModel):
    """Cleaned, structured problem for the user to review/edit before ingesting."""

    statement: str
    constraints: str | None
    samples: list[SampleTestIn]
    # CF "Note" (explains the samples), kept separate so it renders after them.
    note: str | None
    time_limit_ms: int | None
    memory_limit_mb: int | None


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
    note_text: str | None = None
    figures: list[Figure] | None = None
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
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
    note_text: str | None
    figures: list[dict]
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


class ProblemSummary(BaseModel):
    """A lightweight row for the problems list — just enough to show and link. Deliberately
    omits the heavy fields (statement, figures, samples) so a page stays small."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    source: str
    external_id: str
    rating: int | None
    tags: list[str]
    ingestion_status: str
    created_at: datetime
    level_count: int = 0


class ProblemListOut(BaseModel):
    """A page of problems, most-recent first."""

    items: list[ProblemSummary]
    total: int
    page: int
    page_size: int


# --- Tutor (step 6) ----------------------------------------------------------
class RevealedLevel(BaseModel):
    """An UNLOCKED ladder rung, with how it was unlocked. Only revealed levels are ever
    serialized — locked content stays server-side (the no-leakage guarantee).

    `via` drives the design's earned (green) vs opened (violet) treatment; `earned_text` is
    the student's own words the judge matched (chat unlocks only), quoted back in the unlock
    card. `earned_message_id` + `unlocked_at` let the client place the card in the transcript
    after a reload."""

    level: int
    content: str
    via: str  # "chat" (earned) | "manual" (opened)
    earned_text: str | None = None
    earned_message_id: int | None = None
    unlocked_at: datetime | None = None


class TutorMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str  # "user" | "assistant"
    content: str
    # Grounding chunks the assistant cited: [{source, title, url, page}].
    citations: list[dict] | None = None
    created_at: datetime


class TutorSessionOut(BaseModel):
    """Full tutor state for a problem: which rungs are unlocked (with content + how), how
    many remain, the run status, and the durable chat history."""

    slug: str
    status: str  # "active" | "solved" | "gave_up"
    revealed_levels: list[int]
    revealed: list[RevealedLevel]  # unlocked rung contents, in order
    level_count: int  # total rungs (so the UI can show N locked)
    messages: list[TutorMessageOut]


class RevealOut(BaseModel):
    """Result of a manual sequential reveal — the newly unlocked rung + the updated set."""

    level: int
    content: str
    via: str = "manual"
    revealed_levels: list[int]


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


# --- Grounding sources (bring your own references) ---------------------------
class GithubSourceCreate(BaseModel):
    """Add a public GitHub repo as a grounding corpus. Markdown/text files under it are
    chunked + embedded. `license_ack` is the on-page consent (self-hosted → user is admin)."""

    label: str = Field(min_length=1, max_length=80)
    repo_url: str = Field(min_length=1, max_length=512)
    license_ack: bool = False


class GroundingSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str  # "github" | "pdf"
    label: str
    source_key: str
    location: str
    status: str  # pending | cloning | embedding | ready | failed
    error: str | None
    chunk_count: int
    created_at: datetime


class GroundingSourceListOut(BaseModel):
    items: list[GroundingSourceOut]
