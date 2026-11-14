"""Database models. Keep in sync with alembic/versions/."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ANALYSIS_STATUSES = ("queued", "running", "completed", "failed")


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # GitHub limits: owner <= 39 chars, repo name <= 100 chars.
    owner: Mapped[str] = mapped_column(String(39), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    full_name: Mapped[str] = mapped_column(String(140), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    runs: Mapped[list["WorkflowRun"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )


# GitHub repository names are case-insensitive, so uniqueness is too.
Index("uq_repositories_full_name_lower", func.lower(Repository.full_name), unique=True)


class WorkflowRun(Base):
    """A GitHub Actions workflow run. `id` is GitHub's own run id (not auto-generated)."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_repository_id_created_at", "repository_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # When the run was created on GitHub (not when DevPilot stored it).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="runs")
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class Analysis(Base):
    """AI analysis of a run. Table exists from milestone 1; it is filled from milestone 2."""

    __tablename__ = "analyses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="ck_analyses_status"
        ),
        Index("ix_analyses_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    run: Mapped[WorkflowRun] = relationship(back_populates="analyses")
