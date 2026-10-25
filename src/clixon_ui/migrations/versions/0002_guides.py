"""guides and guide runs

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_guides_name", "guides", ["name"], unique=True)
    op.create_table(
        "guide_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("guide_id", sa.Integer(), nullable=True),
        sa.Column("guide_name", sa.String(120), nullable=False),
        sa.Column("guide_version", sa.Integer(), nullable=False),
        sa.Column("guide", sa.JSON(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_guide_runs_guide_id", "guide_runs", ["guide_id"])
    op.create_index("ix_guide_runs_username", "guide_runs", ["username"])
    op.create_index("ix_guide_runs_status", "guide_runs", ["status"])


def downgrade() -> None:
    op.drop_table("guide_runs")
    op.drop_table("guides")
