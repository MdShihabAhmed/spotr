"""level unlocks

Adds `level_unlocks` — how each ladder rung was unlocked (earned via chat vs opened
manually), with the earned message for the unlock-card quote. `TutorSession.revealed_levels`
stays the authority for *which* levels are unlocked; this table is the UX metadata behind the
design's earned (green) / opened (violet) distinction. See app/models.py `LevelUnlock`.

Revision ID: 0004_level_unlocks
Revises: 0003_ingestion_queue
Create Date: 2026-07-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_level_unlocks"
down_revision: Union[str, None] = "0003_ingestion_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "level_unlocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("tutor_sessions.id"), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("via", sa.String(length=16), nullable=False),
        sa.Column(
            "earned_message_id",
            sa.Integer(),
            sa.ForeignKey("session_messages.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", "level", name="uq_level_unlocks_session_level"),
    )
    op.create_index("ix_level_unlocks_session_id", "level_unlocks", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_level_unlocks_session_id", table_name="level_unlocks")
    op.drop_table("level_unlocks")
