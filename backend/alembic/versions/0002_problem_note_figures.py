"""problem note + figures

Adds `note_text` (CF "Note", shown after the samples) and `figures` (user-uploaded
diagrams the AI can't reproduce as text: [{data_uri, section}]) to `problems`.

Revision ID: 0002_note_figures
Revises: 0001_baseline
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_note_figures"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("problems", sa.Column("note_text", sa.Text(), nullable=True))
    # server_default so the NOT NULL add is safe on any existing rows; the app always
    # supplies a concrete list on insert.
    op.add_column(
        "problems",
        sa.Column("figures", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("problems", "figures")
    op.drop_column("problems", "note_text")
