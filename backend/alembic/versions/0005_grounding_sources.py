"""grounding sources

Adds `grounding_sources` — user-added grounding corpora ("bring your own references"): a
GitHub repo or an uploaded PDF the user points Spotr at, cloned/parsed/chunked/embedded into
the same Qdrant collection as the built-in corpora. The row IS the ingestion job (durable
queue, like `problems`): status pending → cloning → embedding → ready/failed, with
`claimed_at`/`attempts` for claim + crash recovery. See app/models.py `GroundingSource`.

Revision ID: 0005_grounding_sources
Revises: 0004_level_unlocks
Create Date: 2026-08-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_grounding_sources"
down_revision: Union[str, None] = "0004_level_unlocks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grounding_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("source_key", sa.String(length=32), nullable=False),
        sa.Column("location", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("license_ack", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("source_key", name="uq_grounding_sources_source_key"),
    )
    op.create_index("ix_grounding_sources_source_key", "grounding_sources", ["source_key"])
    op.create_index("ix_grounding_sources_status", "grounding_sources", ["status"])


def downgrade() -> None:
    op.drop_index("ix_grounding_sources_status", table_name="grounding_sources")
    op.drop_index("ix_grounding_sources_source_key", table_name="grounding_sources")
    op.drop_table("grounding_sources")
