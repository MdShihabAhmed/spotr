"""Database models for the grounded Socratic tutor (six tables).

The six-table schema:
- `Problem` is **source-agnostic** — a `slug` ("{source}-{external_id}", e.g. "cf-1846C")
  is the public/route id; `source` + `external_id` are the identity. Codeforces-specific
  bits live in `source_metadata` so the CF API path needs no re-parsing.
- `HintLevel` is the fixed graduated ladder revealed from (renamed from EditorialHint).
- `TutorSession.revealed_levels` is a disclosure **set**, not a `current_level` ceiling —
  manual reveal fills it sequentially, chat unlocks single matched levels.
- `GroundingChunk` is a section-level chunk of a reference corpus, with provenance.
The old `StressTestRun` / `ConceptNote(topic unique)` shapes are gone (deferred / reworked).
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    # Optional, for a possible future personalization; harmless now.
    cf_handle: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sessions: Mapped[list["TutorSession"]] = relationship(back_populates="user")


class Problem(Base):
    __tablename__ = "problems"
    # Identity is (source, external_id); `slug` is the human-readable projection of it.
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # "cf-1846C"
    source: Mapped[str] = mapped_column(String(32))  # "codeforces"
    external_id: Mapped[str] = mapped_column(String(64))  # "1846C"
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # canonical, display only
    # Source-specific identifiers so API calls don't re-parse the URL (CF: {contest_id, index}).
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    title: Mapped[str] = mapped_column(String(255), default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    statement_text: Mapped[str] = mapped_column(Text, default="")
    constraints_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # list of {"input": str, "expected_output": str} — required to reach `ready`
    sample_tests: Mapped[list] = mapped_column(JSON, default=list)
    # CF "Note": explains the sample cases, so it's shown AFTER the samples (not folded
    # into the statement). Kept as its own field for correct placement + rendering.
    note_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # User-uploaded diagrams the AI can't reproduce as text: [{"data_uri", "section"}]
    # where section is "statement" | "note". Displayed inline; not fed to the tutor (yet).
    figures: Mapped[list] = mapped_column(JSON, default=list)
    time_limit_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    editorial_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Raw editorial — stored server-side, NEVER serialized to clients (spoiler guard).
    editorial_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_interactive: Mapped[bool] = mapped_column(Boolean, default=False)
    has_multiple_answers: Mapped[bool] = mapped_column(Boolean, default=False)
    # "pending" | "normalizing" | "generating_levels" | "ready" | "failed"
    # The row IS the durable job queue: `pending` = waiting to be claimed, the two middle
    # states = claimed/in-flight, `ready`/`failed` = terminal. A worker claims pending rows
    # with SELECT … FOR UPDATE SKIP LOCKED, so the queue survives restarts/crashes.
    ingestion_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    ingestion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When a worker claimed this job. Used for crash recovery: a row stuck in an in-flight
    # state past a timeout (worker died mid-job) is re-queued. NULL when not claimed.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Claim count — caps retries so a job that crashes the worker every time eventually
    # lands in `failed` instead of re-queuing forever.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    levels: Mapped[list["HintLevel"]] = relationship(back_populates="problem")

    @property
    def has_editorial(self) -> bool:
        """Exposed to clients instead of the raw editorial text, which must never leave
        the server (it would spoil the graduated ladder)."""
        return bool(self.editorial_text)


class HintLevel(Base):
    """One rung of a problem's fixed graduated ladder (1–4). The conversation reveals
    from these pre-authored texts, so nothing free-form is ever leaked."""

    __tablename__ = "hint_levels"
    __table_args__ = (UniqueConstraint("problem_id", "level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    level: Mapped[int] = mapped_column(Integer)  # 1–4
    content: Mapped[str] = mapped_column(Text)

    problem: Mapped["Problem"] = relationship(back_populates="levels")


class TutorSession(Base):
    __tablename__ = "tutor_sessions"
    __table_args__ = (UniqueConstraint("user_id", "problem_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    # The disclosure SET, e.g. [1, 2] or [1, 3] — the levels the tutor may discuss.
    # Manual reveal fills it sequentially; a chat demonstration unlocks a single level.
    revealed_levels: Mapped[list] = mapped_column(JSON, default=list)
    # "active" | "solved" | "gave_up"
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="sessions")
    problem: Mapped["Problem"] = relationship()
    messages: Mapped[list["SessionMessage"]] = relationship(back_populates="session")


class SessionMessage(Base):
    """Durable tutor chat history."""

    __tablename__ = "session_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("tutor_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    # Grounding chunks referenced in an assistant turn: [{source, url, section, page}].
    citations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["TutorSession"] = relationship(back_populates="messages")


class GroundingChunk(Base):
    """A section-level chunk of a reference corpus (CP-Algorithms / USACO Guide /
    CP Handbook / PyRival), embedded in Qdrant. Carries provenance for citations."""

    __tablename__ = "grounding_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # "cp-algorithms" | "usaco-guide" | ...
    title: Mapped[str] = mapped_column(String(255))  # section heading
    url: Mapped[str] = mapped_column(String(512))  # exact source URL / repo path
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Handbook page, if any
    content: Mapped[str] = mapped_column(Text)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
