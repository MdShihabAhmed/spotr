"""ingestion queue fields

Adds durable-job-queue bookkeeping to `problems`: `claimed_at` (when a worker claimed the
row - used for crash recovery) and `attempts` (claim count, caps retries). Also indexes
`ingestion_status` so a worker's `WHERE ingestion_status='pending' … FOR UPDATE SKIP LOCKED`
claim stays cheap. See app/queue.py.

Revision ID: 0003_ingestion_queue
Revises: 0002_note_figures
Create Date: 2026-07-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_ingestion_queue"
down_revision: Union[str, None] = "0002_note_figures"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "problems", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    # server_default 0 so the NOT NULL add is safe on existing rows; the app defaults to 0.
    op.add_column(
        "problems",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_problems_ingestion_status", "problems", ["ingestion_status"])


def downgrade() -> None:
    op.drop_index("ix_problems_ingestion_status", table_name="problems")
    op.drop_column("problems", "attempts")
    op.drop_column("problems", "claimed_at")
