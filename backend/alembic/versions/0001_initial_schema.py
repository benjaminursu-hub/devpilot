"""Initial schema: repositories, workflow_runs, analyses.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner", sa.String(39), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("full_name", sa.String(140), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_repositories_full_name_lower",
        "repositories",
        [sa.text("lower(full_name)")],
        unique=True,
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("conclusion", sa.String(32), nullable=True),
        sa.Column("branch", sa.String(255), nullable=True),
        sa.Column("html_url", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workflow_runs_repository_id_created_at",
        "workflow_runs",
        ["repository_id", "created_at"],
    )

    op.create_table(
        "analyses",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "result_json", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="ck_analyses_status"
        ),
    )
    op.create_index("ix_analyses_run_id", "analyses", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_analyses_run_id", table_name="analyses")
    op.drop_table("analyses")
    op.drop_index("ix_workflow_runs_repository_id_created_at", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("uq_repositories_full_name_lower", table_name="repositories")
    op.drop_table("repositories")
