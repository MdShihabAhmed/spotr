"""grounding source retry_at

Adds `grounding_sources.retry_at` for the auto-retry lifecycle: a source that hits a
rate-limit/quota during embedding is set `status='retrying'` with `retry_at` ~24h out (the
free-tier daily reset); other errors back off briefly. A `retrying` row is re-claimed once
`retry_at` passes, and resume skips already-embedded chunks. See app/models.py `GroundingSource`.

Revision ID: 0006_source_retry_at
Revises: 0005_grounding_sources
Create Date: 2026-08-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_source_retry_at"
down_revision: Union[str, None] = "0005_grounding_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grounding_sources",
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("grounding_sources", "retry_at")
