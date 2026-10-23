"""Чаты команды: групповые чаты «МАКС» с ботом, правила «кому нужен», отметка «для представлений».

Revision ID: d4e1a7c93f20
Revises: c2d5e7a90b11
Create Date: 2026-10-23 21:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4e1a7c93f20"
down_revision = "c2d5e7a90b11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "team_chats",
        sa.Column("chat_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("link", sa.String(500), nullable=True),
        sa.Column("members_count", sa.Integer(), nullable=True),
        sa.Column("audience", sa.String(16), server_default="none", nullable=False),
        sa.Column("sites", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("work_type", sa.String(16), nullable=True),
        sa.Column("for_intro", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("prompt_mid", sa.String(128), nullable=True),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("info_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("chat_id"),
    )


def downgrade() -> None:
    op.drop_table("team_chats")
