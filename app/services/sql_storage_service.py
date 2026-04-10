from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.models import ReviewIncident, ReviewResult, UploadedVideo
    from app.db.session import db_session_manager

    SQL_DRIVER_READY = True
except ImportError:  # pragma: no cover
    select = None
    SQLAlchemyError = Exception
    ReviewIncident = None
    ReviewResult = None
    UploadedVideo = None
    db_session_manager = None
    SQL_DRIVER_READY = False


class SQLStorageService:
    def initialize(self) -> bool:
        if not SQL_DRIVER_READY or db_session_manager is None:
            return False
        return db_session_manager.initialize()

    def is_available(self) -> bool:
        if not SQL_DRIVER_READY or db_session_manager is None:
            return False
        return bool(db_session_manager.engine) or self.initialize()

    def get_status(self) -> dict[str, Any]:
        configured = db_session_manager.is_configured() if db_session_manager is not None else False
        available = self.is_available() if configured else False
        return {
            "dependency_ready": SQL_DRIVER_READY,
            "configured": configured,
            "enabled": available,
            "env_name": db_session_manager.env_name if db_session_manager is not None else "SQLSERVER_URL",
            "error": db_session_manager.init_error if db_session_manager is not None else None,
        }

    def save_upload(self, upload_info: dict[str, Any]) -> int | None:
        if not self.is_available() or select is None or UploadedVideo is None:
            return None

        stored_filename = str(upload_info.get("stored_filename") or "")
        if not stored_filename:
            return None

        try:
            with db_session_manager.session_scope() as session:
                existing = session.scalar(
                    select(UploadedVideo).where(UploadedVideo.stored_filename == stored_filename)
                )
                if existing is None:
                    existing = UploadedVideo(
                        original_filename=str(upload_info.get("original_filename") or stored_filename),
                        stored_filename=stored_filename,
                        file_path=str(upload_info.get("path") or ""),
                        size_bytes=int(upload_info.get("size_bytes") or 0),
                    )
                    session.add(existing)
                    session.flush()
                    return int(existing.id)

                existing.original_filename = str(upload_info.get("original_filename") or existing.original_filename)
                existing.file_path = str(upload_info.get("path") or existing.file_path)
                existing.size_bytes = int(upload_info.get("size_bytes") or existing.size_bytes or 0)
                session.flush()
                return int(existing.id)
        except (SQLAlchemyError, RuntimeError):
            return None

    def _resolve_upload_id(self, session, upload_info: dict[str, Any] | None, video_path: str) -> int | None:
        if select is None or UploadedVideo is None:
            return None
        if upload_info:
            stored_filename = str(upload_info.get("stored_filename") or "").strip()
            if stored_filename:
                upload = session.scalar(select(UploadedVideo).where(UploadedVideo.stored_filename == stored_filename))
                if upload is not None:
                    return int(upload.id)

            raw_path = str(upload_info.get("path") or "").strip()
            if raw_path:
                upload = session.scalar(select(UploadedVideo).where(UploadedVideo.file_path == raw_path))
                if upload is not None:
                    return int(upload.id)

        upload = session.scalar(select(UploadedVideo).where(UploadedVideo.file_path == video_path))
        return int(upload.id) if upload is not None else None

    def save_review_result(self, result_payload: dict[str, Any], upload_info: dict[str, Any] | None = None) -> int | None:
        if not self.is_available() or select is None or ReviewResult is None or ReviewIncident is None:
            return None

        video_path = str(result_payload.get("video_path") or "")
        summary = result_payload.get("summary", {}) or {}
        engines = result_payload.get("engines", {}) or {}
        incidents = result_payload.get("incidents", []) or []

        try:
            with db_session_manager.session_scope() as session:
                upload_id = self._resolve_upload_id(session, upload_info=upload_info, video_path=video_path)
                review = ReviewResult(
                    upload_id=upload_id,
                    video_path=video_path,
                    result_path=str(result_payload.get("result_path") or ""),
                    status=str(result_payload.get("status") or "unknown"),
                    analysis_mode=str(result_payload.get("analysis_mode") or "n/a"),
                    message=str(result_payload.get("message") or ""),
                    total_violations=int(summary.get("total_violations") or 0),
                    reviewed_frames=int(summary.get("reviewed_frames") or 0),
                    summary_json=json.dumps(summary, ensure_ascii=False),
                    engines_json=json.dumps(engines, ensure_ascii=False),
                )
                session.add(review)
                session.flush()

                for incident in incidents:
                    event = ReviewIncident(
                        review_id=int(review.id),
                        time_label=str(incident.get("time") or ""),
                        time_seconds=float(incident["time_seconds"]) if incident.get("time_seconds") is not None else None,
                        label=str(incident.get("label") or ""),
                        confidence=str(incident.get("confidence") or ""),
                        risk=str(incident.get("risk") or ""),
                        event_type=str(incident.get("event_type") or ""),
                        snapshot_url=str(incident.get("snapshot_url") or ""),
                        details=str(incident.get("details") or ""),
                    )
                    session.add(event)

                session.flush()
                return int(review.id)
        except (SQLAlchemyError, RuntimeError, ValueError, TypeError):
            return None

    def _parse_json_text(self, raw_value: str | None) -> dict[str, Any]:
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _normalize_created_at(self, created_at: datetime | None) -> str:
        if created_at is None:
            return ""
        return created_at.isoformat()

    def get_latest_review_result(self) -> dict[str, Any] | None:
        if not self.is_available() or select is None or ReviewResult is None or ReviewIncident is None:
            return None

        try:
            with db_session_manager.session_scope() as session:
                review = session.scalar(select(ReviewResult).order_by(ReviewResult.created_at.desc()))
                if review is None:
                    return None
                incidents = [
                    incident.to_payload()
                    for incident in session.scalars(
                        select(ReviewIncident).where(ReviewIncident.review_id == review.id).order_by(ReviewIncident.id.asc())
                    ).all()
                ]
                summary_payload = self._parse_json_text(review.summary_json)
                return {
                    "status": review.status,
                    "analysis_mode": review.analysis_mode,
                    "video_path": review.video_path,
                    "summary": summary_payload,
                    "students_report": summary_payload.get("students_report", []),
                    "primary_candidate": summary_payload.get("primary_candidate"),
                    "incidents": incidents,
                    "engines": self._parse_json_text(review.engines_json),
                    "message": review.message,
                    "result_path": review.result_path,
                    "created_at": self._normalize_created_at(review.created_at),
                }
        except (SQLAlchemyError, RuntimeError):
            return None

    def list_recent_reviews(self, limit: int = 5) -> list[dict[str, Any]]:
        if not self.is_available() or select is None or ReviewResult is None:
            return []

        try:
            with db_session_manager.session_scope() as session:
                rows = session.scalars(
                    select(ReviewResult).order_by(ReviewResult.created_at.desc()).limit(max(1, limit))
                ).all()

                results: list[dict[str, Any]] = []
                for row in rows:
                    result_filename = ""
                    if row.result_path:
                        result_filename = Path(row.result_path).name
                    elif row.video_path:
                        result_filename = f"{Path(row.video_path).stem}.json"
                    results.append(
                        {
                            "filename": result_filename,
                            "status": row.status,
                            "violations": row.total_violations,
                            "analysis_mode": row.analysis_mode,
                            "created_at": self._normalize_created_at(row.created_at),
                        }
                    )
                return results
        except (SQLAlchemyError, RuntimeError):
            return []


sql_storage_service = SQLStorageService()
