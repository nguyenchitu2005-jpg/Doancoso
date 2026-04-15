from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UploadedVideo(Base):
    __tablename__ = "uploaded_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_filename: Mapped[str] = mapped_column(String(260), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(260), unique=True, index=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    reviews: Mapped[list["ReviewResult"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        order_by="ReviewResult.created_at.desc()",
    )


class ReviewResult(Base):
    __tablename__ = "review_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_videos.id"), nullable=True, index=True)
    video_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    result_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    video_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown", index=True)
    analysis_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="n/a")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    total_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reviewed_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    teacher_decision: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    teacher_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    engines_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    upload: Mapped[UploadedVideo | None] = relationship(back_populates="reviews")
    incidents: Mapped[list["ReviewIncident"]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        order_by="ReviewIncident.id.asc()",
    )


class ReviewIncident(Base):
    __tablename__ = "review_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("review_results.id"), nullable=False, index=True)
    time_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    time_seconds: Mapped[float | None] = mapped_column(nullable=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    confidence: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    snapshot_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    review: Mapped[ReviewResult] = relationship(back_populates="incidents")

    def to_payload(self) -> dict[str, Any]:
        return {
            "time": self.time_label,
            "time_seconds": self.time_seconds,
            "label": self.label,
            "confidence": self.confidence,
            "risk": self.risk,
            "event_type": self.event_type,
            "snapshot_url": self.snapshot_url,
            "details": self.details,
        }


class CandidateHistory(Base):
    __tablename__ = "candidate_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    room: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_reviews: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_alerts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    behaviors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    last_review_id: Mapped[int | None] = mapped_column(ForeignKey("review_results.id"), nullable=True, index=True)

    last_review: Mapped[ReviewResult | None] = relationship(foreign_keys=[last_review_id])
    incidents: Mapped[list["CandidateIncidentHistory"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="CandidateIncidentHistory.created_at.desc()",
    )


class CandidateIncidentHistory(Base):
    __tablename__ = "candidate_incident_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_history_id: Mapped[int] = mapped_column(ForeignKey("candidate_histories.id"), nullable=False, index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("review_results.id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    candidate_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    candidate_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate_room: Mapped[str | None] = mapped_column(String(255), nullable=True)
    time_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    time_seconds: Mapped[float | None] = mapped_column(nullable=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    confidence: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    snapshot_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    candidate: Mapped[CandidateHistory] = relationship(back_populates="incidents")
    review: Mapped[ReviewResult] = relationship(foreign_keys=[review_id])

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_name": self.candidate_name,
            "candidate_email": self.candidate_email,
            "candidate_room": self.candidate_room,
            "time": self.time_label,
            "time_seconds": self.time_seconds,
            "label": self.label,
            "confidence": self.confidence,
            "risk": self.risk,
            "event_type": self.event_type,
            "snapshot_url": self.snapshot_url,
            "details": self.details,
            "review_id": self.review_id,
            "created_at": self.created_at.isoformat() if self.created_at is not None else "",
        }
