"""Поля для модулей: статус ответа HR, ключ и подпись фрагмента, id сообщений для правки на месте.

Revision ID: c2d5e7a90b11
Revises: bc48dd70294e
Create Date: 2026-10-23 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d5e7a90b11"
down_revision = "bc48dd70294e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hr_answers", sa.Column("status", sa.String(16), server_default="draft", nullable=False))
    op.add_column("kb_chunks", sa.Column("chunk_key", sa.String(64), nullable=True))
    op.add_column("kb_chunks", sa.Column("source_label", sa.Text(), nullable=True))
    op.create_index("ix_kb_chunks_chunk_key", "kb_chunks", ["chunk_key"])
    op.add_column("tickets", sa.Column("group_mid", sa.String(128), nullable=True))
    op.add_column("manager_tasks", sa.Column("mid", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("manager_tasks", "mid")
    op.drop_column("tickets", "group_mid")
    op.drop_index("ix_kb_chunks_chunk_key", table_name="kb_chunks")
    op.drop_column("kb_chunks", "source_label")
    op.drop_column("kb_chunks", "chunk_key")
    op.drop_column("hr_answers", "status")
