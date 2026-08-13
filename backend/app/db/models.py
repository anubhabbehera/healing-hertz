from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="running")  # running|completed|failed
    trigger: Mapped[str] = mapped_column(String, default="manual")
    site_id: Mapped[str | None] = mapped_column(String, nullable=True)
    site_name: Mapped[str | None] = mapped_column(String, nullable=True)
    application_version: Mapped[str | None] = mapped_column(String, nullable=True)
    health_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    advice_status: Mapped[str] = mapped_column(String, default="skipped")  # ok|skipped|failed
    advice_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    advice_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    unsupported_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    findings: Mapped[list["FindingRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    suggestions: Mapped[list["SuggestionRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    metrics: Mapped[list["MetricSnapshot"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    recommendation: Mapped[str] = mapped_column(Text)
    subject_type: Mapped[str] = mapped_column(String, default="site")
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True)
    subject_name: Mapped[str | None] = mapped_column(String, nullable=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[ScanRun] = relationship(back_populates="findings")


class Dismissal(Base):
    """An operator's standing judgement that a finding is won't-fix.

    Keyed by (rule_id, subject_id); a NULL subject_id dismisses the rule for
    every subject. Dismissed findings are still reported, but cost no score.
    """

    __tablename__ = "dismissals"
    __table_args__ = (UniqueConstraint("rule_id", "subject_id", name="uq_dismissal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String, index=True)
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True)
    subject_name: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SuggestionRow(Base):
    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    priority: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)
    rationale: Mapped[str] = mapped_column(Text)
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    effort: Mapped[str] = mapped_column(String, default="medium")
    related_rule_ids_json: Mapped[str] = mapped_column(Text, default="[]")

    run: Mapped[ScanRun] = relationship(back_populates="suggestions")


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id"), index=True)
    subject_type: Mapped[str] = mapped_column(String)
    subject_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    subject_name: Mapped[str | None] = mapped_column(String, nullable=True)
    metric: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[float] = mapped_column(Float)

    run: Mapped[ScanRun] = relationship(back_populates="metrics")
