"""grounding source chunks_total

Adds `grounding_sources.chunks_total` so the UI can show live embedding progress (chunk_count
= embedded so far, chunks_total = total to embed; 0 until known). See app/models.py.

Revision ID: 0007_source_chunks_total
Revises: 0006_source_retry_at
Create Date: 2026-08-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_source_chunks_total"
down_revision: Union[str, None] = "0006_source_retry_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grounding_sources",
        sa.Column("chunks_total", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("grounding_sources", "chunks_total")
