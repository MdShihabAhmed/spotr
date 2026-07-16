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
    cf_handle: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    sessions: Mapped[list["HintSession"]] = relationship(back_populates="user")


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (UniqueConstraint("cf_contest_id", "cf_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cf_url: Mapped[str] = mapped_column(String(512), unique=True)
    cf_contest_id: Mapped[int] = mapped_column(Integer)
    cf_index: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(255), default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    statement_text: Mapped[str] = mapped_column(Text, default="")
    constraints_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # list of {"input": str, "expected_output": str} — ground truth for validating
    # the LLM-generated brute force before it is trusted in stress tests
    sample_tests: Mapped[list] = mapped_column(JSON, default=list)
    time_limit_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    memory_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    editorial_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    editorial_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_interactive: Mapped[bool] = mapped_column(Boolean, default=False)
    has_multiple_answers: Mapped[bool] = mapped_column(Boolean, default=False)
    # "pending" | "scraping" | "generating_hints" | "ready" | "failed"
    ingestion_status: Mapped[str] = mapped_column(String(32), default="pending")
    ingestion_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hints: Mapped[list["EditorialHint"]] = relationship(back_populates="problem")


class EditorialHint(Base):
    __tablename__ = "editorial_hints"
    __table_args__ = (UniqueConstraint("problem_id", "level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    level: Mapped[int] = mapped_column(Integer)  # 1–4
    content: Mapped[str] = mapped_column(Text)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    problem: Mapped["Problem"] = relationship(back_populates="hints")


class HintSession(Base):
    __tablename__ = "hint_sessions"
    __table_args__ = (UniqueConstraint("user_id", "problem_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"))
    current_level: Mapped[int] = mapped_column(Integer, default=0)  # 0 = no hints revealed yet
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
    """Durable chat history for both hint clarification Q&A and the pitfall dialogue."""

    __tablename__ = "session_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("hint_sessions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # "clarification" | "pitfall"
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    hint_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["HintSession"] = relationship(back_populates="messages")


class StressTestRun(Base):
    __tablename__ = "stress_test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("hint_sessions.id"), index=True)
    submitted_code: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(16))  # whitelist, e.g. "cpp17" | "python3"
    brute_force_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    brute_force_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    num_cases_run: Mapped[int] = mapped_column(Integer, default=0)
    counter_example_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "passed_stress_test" | "counter_example_found" | "brute_force_generation_failed" | "user_code_error"
    verdict: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConceptNote(Base):
    __tablename__ = "concept_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(64), unique=True)
    summary: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(512))
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
