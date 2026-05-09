from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from sqlalchemy import delete, select
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.models import CandidateHistory, CandidateIncidentHistory, ReviewIncident, ReviewResult, UploadedVideo
    from app.db.session import db_session_manager

    SQL_DRIVER_READY = True
except ImportError:  # pragma: no cover
    delete = None
    select = None
    SQLAlchemyError = Exception
    CandidateHistory = None
    CandidateIncidentHistory = None
    ReviewIncident = None
    ReviewResult = None
    UploadedVideo = None
    db_session_manager = None
    SQL_DRIVER_READY = False


class SQLStorageService:
    def _teacher_review_payload(self, decision: str | None, decided_at: datetime | None) -> dict[str, Any]:
        # Chuan hoa teacher decision ve mot payload thong nhat de UI/API de dung lai.
        normalized = str(decision or "").strip().lower()
        if normalized not in {"confirmed", "dismissed"}:
            normalized = "pending"
        label_map = {
            "pending": "Chua quyet dinh",
            "confirmed": "Da xac nhan gian lan",
            "dismissed": "Da bo qua",
        }
        return {
            "status": normalized,
            "label": label_map[normalized],
            "decided_at": self._normalize_created_at(decided_at) or None,
        }

    def _risk_rank(self, risk: str | None) -> int:
        # Helper de so sanh/cap nhat risk theo thu tu high > medium > low.
        return {"high": 3, "medium": 2, "low": 1}.get(str(risk or "low"), 1)

    def _merge_risk(self, left: str | None, right: str | None) -> str:
        return str(left or "low") if self._risk_rank(left) >= self._risk_rank(right) else str(right or "low")

    def _risk_from_total_alerts(self, total_alerts: int, current_risk: str | None) -> str:
        risk = str(current_risk or "low")
        if total_alerts >= 4:
            return self._merge_risk(risk, "high")
        if total_alerts >= 2:
            return self._merge_risk(risk, "medium")
        return risk

    def _normalize_candidate_id(self, raw_value: Any) -> str:
        # Loai bo UNKNOWN/rong de tranh ghi rac vao bang lich su.
        candidate_id = str(raw_value or "").strip()
        if not candidate_id or candidate_id == "UNKNOWN":
            return ""
        return candidate_id

    def _normalize_behaviors(self, raw_value: Any) -> list[str]:
        # Chuan hoa danh sach behavior de tranh trung lap khi merge lich su.
        if not isinstance(raw_value, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_value:
            behavior = str(item or "").strip()
            if not behavior or behavior in seen:
                continue
            seen.add(behavior)
            normalized.append(behavior)
        return normalized

    def _merge_behaviors(self, existing: list[str], incoming: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for item in [*existing, *incoming]:
            behavior = str(item or "").strip()
            if not behavior or behavior in seen:
                continue
            seen.add(behavior)
            merged.append(behavior)
        return merged

    def _normalize_video_hash(self, raw_value: Any) -> str:
        normalized = str(raw_value or "").strip().lower()
        return normalized if normalized else ""

    def _compute_file_sha256(self, file_path: str | Path | None) -> str:
        # Hash video duoc dung de nhan dien video trung khi backfill/luu DB.
        if not file_path:
            return ""
        target_path = Path(file_path)
        if not target_path.exists() or not target_path.is_file():
            return ""

        digest = hashlib.sha256()
        try:
            with target_path.open("rb") as source_file:
                while True:
                    chunk = source_file.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError:
            return ""
        return digest.hexdigest()

    def _resolve_video_hash(
        self,
        video_path: str,
        result_payload: dict[str, Any] | None = None,
        upload_info: dict[str, Any] | None = None,
    ) -> str:
        # Uu tien video_hash co san; neu khong co thi tinh lai tu file video.
        payload_hash = self._normalize_video_hash((result_payload or {}).get("video_hash"))
        if payload_hash:
            return payload_hash

        upload_hash = self._normalize_video_hash((upload_info or {}).get("content_hash"))
        if upload_hash:
            return upload_hash

        return self._compute_file_sha256(video_path)

    def _build_candidate_rollups(self, result_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        # Bien ket qua mot lan review thanh "tong hop theo candidate"
        # de cap nhat CandidateHistory de dang hon.
        summary = result_payload.get("summary", {}) or {}
        students_report = result_payload.get("students_report") or summary.get("students_report") or []
        incidents = result_payload.get("incidents", []) or []
        rollups: dict[str, dict[str, Any]] = {}

        for student in students_report:
            if not isinstance(student, dict):
                continue
            candidate_id = self._normalize_candidate_id(student.get("candidate_id"))
            if not candidate_id:
                continue
            rollups[candidate_id] = {
                "candidate_id": candidate_id,
                "name": str(student.get("name") or candidate_id),
                "email": str(student.get("email") or ""),
                "room": str(student.get("room") or ""),
                "alerts": int(student.get("alerts") or 0),
                "risk": str(student.get("risk") or "low"),
                "behaviors": self._normalize_behaviors(student.get("behaviors")),
                "_from_report": True,
            }

        for incident in incidents:
            if not isinstance(incident, dict):
                continue
            candidate_id = self._normalize_candidate_id(incident.get("candidate_id"))
            if not candidate_id:
                continue
            row = rollups.get(candidate_id)
            if row is None:
                row = {
                    "candidate_id": candidate_id,
                    "name": str(incident.get("candidate_name") or candidate_id),
                    "email": str(incident.get("candidate_email") or ""),
                    "room": str(incident.get("candidate_room") or ""),
                    "alerts": 0,
                    "risk": str(incident.get("risk") or "low"),
                    "behaviors": [],
                    "_from_report": False,
                }
                rollups[candidate_id] = row
            if not row.get("name"):
                row["name"] = str(incident.get("candidate_name") or candidate_id)
            if not row.get("email"):
                row["email"] = str(incident.get("candidate_email") or "")
            if not row.get("room"):
                row["room"] = str(incident.get("candidate_room") or "")
            row["risk"] = self._merge_risk(str(row.get("risk") or "low"), str(incident.get("risk") or "low"))
            behavior_label = str(incident.get("label") or "").strip()
            if behavior_label and behavior_label not in row["behaviors"]:
                row["behaviors"].append(behavior_label)
            if not bool(row.get("_from_report")):
                row["alerts"] = int(row.get("alerts") or 0) + 1

        return rollups

    def _candidate_profiles_from_result(self, result_payload: dict[str, Any]) -> dict[str, dict[str, str]]:
        # Rut profile candidate tu students_report/primary_candidate de bo sung vao incidents.
        summary = result_payload.get("summary", {}) or {}
        students_report = result_payload.get("students_report") or summary.get("students_report") or []
        primary_candidate = result_payload.get("primary_candidate") or summary.get("primary_candidate") or {}
        profiles: dict[str, dict[str, str]] = {}

        for student in students_report:
            if not isinstance(student, dict):
                continue
            candidate_id = self._normalize_candidate_id(student.get("candidate_id"))
            if not candidate_id:
                continue
            profiles[candidate_id] = {
                "candidate_id": candidate_id,
                "candidate_name": str(student.get("name") or candidate_id),
                "candidate_email": str(student.get("email") or ""),
                "candidate_room": str(student.get("room") or ""),
            }

        if isinstance(primary_candidate, dict):
            candidate_id = self._normalize_candidate_id(primary_candidate.get("candidate_id"))
            if candidate_id and candidate_id not in profiles:
                profiles[candidate_id] = {
                    "candidate_id": candidate_id,
                    "candidate_name": str(primary_candidate.get("name") or candidate_id),
                    "candidate_email": str(primary_candidate.get("email") or ""),
                    "candidate_room": str(primary_candidate.get("room") or ""),
                }

        return profiles

    def _infer_review_candidate_identity(self, result_payload: dict[str, Any]) -> dict[str, str] | None:
        # Fallback xac dinh candidate chinh cua lan review khi incident chua mang du thong tin.
        profiles = self._candidate_profiles_from_result(result_payload)
        if len(profiles) == 1:
            return next(iter(profiles.values()))

        summary = result_payload.get("summary", {}) or {}
        recognized_candidates = int(summary.get("recognized_candidates") or 0)
        primary_candidate = result_payload.get("primary_candidate") or summary.get("primary_candidate") or {}
        candidate_id = self._normalize_candidate_id(primary_candidate.get("candidate_id"))
        if candidate_id and recognized_candidates == 1:
            profile = profiles.get(candidate_id)
            if profile is not None:
                return profile
            return {
                "candidate_id": candidate_id,
                "candidate_name": str(primary_candidate.get("name") or candidate_id),
                "candidate_email": str(primary_candidate.get("email") or ""),
                "candidate_room": str(primary_candidate.get("room") or ""),
            }

        return None

    def _enrich_incidents_with_candidate_context(
        self,
        incidents: list[dict[str, Any]],
        result_payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # Dam bao moi incident, neu co the, deu mang candidate_id/name/email/room day du.
        if not incidents:
            return []

        profiles = self._candidate_profiles_from_result(result_payload)
        fallback_identity = self._infer_review_candidate_identity(result_payload)
        enriched_incidents: list[dict[str, Any]] = []

        for incident in incidents:
            if not isinstance(incident, dict):
                continue

            enriched = dict(incident)
            candidate_id = self._normalize_candidate_id(enriched.get("candidate_id"))
            profile = profiles.get(candidate_id) if candidate_id else None

            if profile is None and candidate_id:
                profile = {
                    "candidate_id": candidate_id,
                    "candidate_name": str(enriched.get("candidate_name") or candidate_id),
                    "candidate_email": str(enriched.get("candidate_email") or ""),
                    "candidate_room": str(enriched.get("candidate_room") or ""),
                }

            if profile is None:
                profile = fallback_identity

            if profile is not None:
                enriched["candidate_id"] = profile["candidate_id"]
                enriched["candidate_name"] = str(enriched.get("candidate_name") or profile["candidate_name"])
                enriched["candidate_email"] = str(enriched.get("candidate_email") or profile["candidate_email"])
                enriched["candidate_room"] = str(enriched.get("candidate_room") or profile["candidate_room"])

            enriched_incidents.append(enriched)

        return enriched_incidents

    def _build_review_payload(self, review, incidents: list[dict[str, Any]]) -> dict[str, Any]:
        # Dung trong backfill/rebuild khi can tai tao payload JSON tu ban ghi SQL.
        summary = self._parse_json_text(review.summary_json)
        return {
            "video_path": review.video_path,
            "video_hash": self._normalize_video_hash(getattr(review, "video_hash", None)),
            "summary": summary,
            "students_report": summary.get("students_report", []),
            "primary_candidate": summary.get("primary_candidate"),
            "incidents": incidents,
        }

    def initialize(self) -> bool:
        # Khoi tao SQLAlchemy engine/session manager.
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
        # Luu metadata file upload vao bang uploaded_videos.
        if not self.is_available() or select is None or UploadedVideo is None:
            return None

        stored_filename = str(upload_info.get("stored_filename") or "")
        content_hash = self._normalize_video_hash(upload_info.get("content_hash"))
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
                        content_hash=content_hash or None,
                        size_bytes=int(upload_info.get("size_bytes") or 0),
                    )
                    session.add(existing)
                    session.flush()
                    return int(existing.id)

                existing.original_filename = str(upload_info.get("original_filename") or existing.original_filename)
                existing.file_path = str(upload_info.get("path") or existing.file_path)
                existing.content_hash = content_hash or existing.content_hash
                existing.size_bytes = int(upload_info.get("size_bytes") or existing.size_bytes or 0)
                session.flush()
                return int(existing.id)
        except (SQLAlchemyError, RuntimeError):
            return None

    def _resolve_upload_id(self, session, upload_info: dict[str, Any] | None, video_path: str) -> int | None:
        # Tim upload_id de lien ket review voi uploaded_videos neu co the.
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

            content_hash = self._normalize_video_hash(upload_info.get("content_hash"))
            if content_hash:
                upload = session.scalar(select(UploadedVideo).where(UploadedVideo.content_hash == content_hash))
                if upload is not None:
                    return int(upload.id)

        upload = session.scalar(select(UploadedVideo).where(UploadedVideo.file_path == video_path))
        return int(upload.id) if upload is not None else None

    def _review_exists_for_result(self, session, result_path: str, video_path: str) -> bool:
        # Helper cu/bo tro de tranh ghi trung mot review khi co result_path/video_path giong nhau.
        if select is None or ReviewResult is None:
            return False
        normalized_result_path = str(result_path or "").strip()
        normalized_video_path = str(video_path or "").strip()
        if normalized_result_path:
            existing = session.scalar(select(ReviewResult).where(ReviewResult.result_path == normalized_result_path))
            if existing is not None:
                return True
        if normalized_video_path:
            existing = session.scalar(select(ReviewResult).where(ReviewResult.video_path == normalized_video_path))
            if existing is not None:
                return True
        return False

    def _has_prior_review_for_video_hash(self, session, video_hash: str) -> bool:
        # Neu video hash da tung co trong DB thi co the day la video trung/backfill lap lai.
        normalized_hash = self._normalize_video_hash(video_hash)
        if not normalized_hash or select is None or ReviewResult is None:
            return False
        existing = session.scalar(select(ReviewResult).where(ReviewResult.video_hash == normalized_hash))
        return existing is not None

    def _apply_candidate_rollups(
        self,
        session,
        review_id: int,
        review_created_at: datetime,
        candidate_rollups: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        # Cap nhat bang CandidateHistory tu ket qua tong hop cua mot lan review.
        if select is None or CandidateHistory is None:
            return {}

        candidate_ids = list(candidate_rollups.keys())
        histories_by_candidate_id: dict[str, Any] = {}
        if candidate_ids:
            histories_by_candidate_id = {
                row.candidate_id: row
                for row in session.scalars(
                    select(CandidateHistory).where(CandidateHistory.candidate_id.in_(candidate_ids))
                ).all()
            }

        for candidate_id, payload in candidate_rollups.items():
            history = histories_by_candidate_id.get(candidate_id)
            if history is None:
                history = CandidateHistory(
                    candidate_id=candidate_id,
                    first_seen_at=review_created_at,
                    last_seen_at=review_created_at,
                    last_review_id=review_id,
                )
                session.add(history)
                histories_by_candidate_id[candidate_id] = history

            merged_behaviors = self._merge_behaviors(
                self._normalize_behaviors(self._parse_json_list(history.behaviors_json)),
                self._normalize_behaviors(payload.get("behaviors")),
            )
            next_total_alerts = int(history.total_alerts or 0) + int(payload.get("alerts") or 0)

            history.name = str(payload.get("name") or history.name or candidate_id)
            history.email = str(payload.get("email") or history.email or "")
            history.room = str(payload.get("room") or history.room or "")
            history.total_reviews = int(history.total_reviews or 0) + 1
            history.total_alerts = next_total_alerts
            history.risk = self._risk_from_total_alerts(
                total_alerts=next_total_alerts,
                current_risk=self._merge_risk(str(history.risk or "low"), str(payload.get("risk") or "low")),
            )
            history.behaviors_json = json.dumps(merged_behaviors, ensure_ascii=False)
            history.last_seen_at = review_created_at
            history.last_review_id = review_id

        return histories_by_candidate_id

    def _record_candidate_incidents(
        self,
        session,
        review_id: int,
        incidents: list[dict[str, Any]],
        histories_by_candidate_id: dict[str, Any],
    ) -> None:
        # Ghi tung incident xuong lich su theo candidate de co the truy van ve sau.
        if CandidateIncidentHistory is None:
            return

        for incident in incidents:
            candidate_id = self._normalize_candidate_id(incident.get("candidate_id"))
            if not candidate_id:
                continue
            history = histories_by_candidate_id.get(candidate_id)
            if history is None:
                continue
            session.add(
                CandidateIncidentHistory(
                    candidate_history_id=int(history.id),
                    review_id=review_id,
                    candidate_id=candidate_id,
                    candidate_name=str(incident.get("candidate_name") or history.name or candidate_id),
                    candidate_email=str(incident.get("candidate_email") or history.email or ""),
                    candidate_room=str(incident.get("candidate_room") or history.room or ""),
                    time_label=str(incident.get("time") or ""),
                    time_seconds=float(incident["time_seconds"]) if incident.get("time_seconds") is not None else None,
                    label=str(incident.get("label") or ""),
                    confidence=str(incident.get("confidence") or ""),
                    risk=str(incident.get("risk") or ""),
                    event_type=str(incident.get("event_type") or ""),
                    snapshot_url=str(incident.get("snapshot_url") or ""),
                    details=str(incident.get("details") or ""),
                )
            )

    def _sync_candidate_history_last_review_refs(self, session, candidate_ids: set[str] | None = None) -> int:
        # Sau khi co incidents, dong bo last_review_id/last_seen_at ve lan review moi nhat cua tung candidate.
        if select is None or CandidateHistory is None or ReviewIncident is None or ReviewResult is None:
            return 0

        normalized_candidate_ids = {
            self._normalize_candidate_id(candidate_id)
            for candidate_id in (candidate_ids or set())
            if self._normalize_candidate_id(candidate_id)
        }

        statement = (
            select(
                ReviewIncident.candidate_id,
                ReviewIncident.review_id,
                ReviewResult.created_at,
            )
            .join(ReviewResult, ReviewResult.id == ReviewIncident.review_id)
            .order_by(
                ReviewIncident.candidate_id.asc(),
                ReviewResult.created_at.desc(),
                ReviewResult.id.desc(),
            )
        )
        if normalized_candidate_ids:
            statement = statement.where(ReviewIncident.candidate_id.in_(normalized_candidate_ids))

        latest_review_by_candidate_id: dict[str, tuple[int, datetime | None]] = {}
        for candidate_id, review_id, created_at in session.execute(statement).all():
            normalized_candidate_id = self._normalize_candidate_id(candidate_id)
            if not normalized_candidate_id or review_id is None:
                continue
            if normalized_candidate_id in latest_review_by_candidate_id:
                continue
            latest_review_by_candidate_id[normalized_candidate_id] = (int(review_id), created_at)

        if not latest_review_by_candidate_id:
            return 0

        histories = session.scalars(
            select(CandidateHistory).where(CandidateHistory.candidate_id.in_(list(latest_review_by_candidate_id.keys())))
        ).all()

        updated_count = 0
        for history in histories:
            latest_review = latest_review_by_candidate_id.get(history.candidate_id)
            if latest_review is None:
                continue
            latest_review_id, latest_created_at = latest_review
            changed = False
            if int(history.last_review_id or 0) != latest_review_id:
                history.last_review_id = latest_review_id
                changed = True
            if latest_created_at is not None and history.last_seen_at != latest_created_at:
                history.last_seen_at = latest_created_at
                changed = True
            if changed:
                updated_count += 1

        return updated_count

    def save_review_result(self, result_payload: dict[str, Any], upload_info: dict[str, Any] | None = None) -> int | None:
        # Luong luu DB chinh:
        # - luu review_results
        # - luu review_incidents
        # - cap nhat candidate_histories
        # - luu candidate_incident_histories
        if (
            not self.is_available()
            or select is None
            or ReviewResult is None
            or ReviewIncident is None
            or CandidateHistory is None
            or CandidateIncidentHistory is None
        ):
            return None

        video_path = str(result_payload.get("video_path") or "")
        summary = result_payload.get("summary", {}) or {}
        engines = result_payload.get("engines", {}) or {}
        incidents = self._enrich_incidents_with_candidate_context(
            result_payload.get("incidents", []) or [],
            result_payload=result_payload,
        )
        candidate_rollups = self._build_candidate_rollups(result_payload)
        video_hash = self._resolve_video_hash(video_path=video_path, result_payload=result_payload, upload_info=upload_info)
        teacher_review = result_payload.get("teacher_review", {}) or {}
        teacher_decision = str(teacher_review.get("status") or "").strip().lower()
        if teacher_decision not in {"confirmed", "dismissed"}:
            teacher_decision = None

        try:
            with db_session_manager.session_scope() as session:
                upload_id = self._resolve_upload_id(session, upload_info=upload_info, video_path=video_path)
                is_duplicate_video = self._has_prior_review_for_video_hash(session, video_hash)
                review = ReviewResult(
                    upload_id=upload_id,
                    video_path=video_path,
                    result_path=str(result_payload.get("result_path") or ""),
                    video_hash=video_hash or None,
                    status=str(result_payload.get("status") or "unknown"),
                    analysis_mode=str(result_payload.get("analysis_mode") or "n/a"),
                    message=str(result_payload.get("message") or ""),
                    total_violations=int(summary.get("total_violations") or 0),
                    reviewed_frames=int(summary.get("reviewed_frames") or 0),
                    teacher_decision=teacher_decision,
                    summary_json=json.dumps(summary, ensure_ascii=False),
                    engines_json=json.dumps(engines, ensure_ascii=False),
                )
                session.add(review)
                session.flush()

                # Ghi tung incident goc cua lan review vao bang review_incidents.
                for incident in incidents:
                    event = ReviewIncident(
                        review_id=int(review.id),
                        candidate_id=self._normalize_candidate_id(incident.get("candidate_id")) or None,
                        candidate_name=str(incident.get("candidate_name") or "") or None,
                        candidate_email=str(incident.get("candidate_email") or "") or None,
                        candidate_room=str(incident.get("candidate_room") or "") or None,
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

                # Video trung thi van luu review moi, nhung bo qua cong don lich su candidate.
                if is_duplicate_video:
                    session.flush()
                    self._sync_candidate_history_last_review_refs(
                        session=session,
                        candidate_ids=set(candidate_rollups.keys()),
                    )
                    session.flush()
                    return int(review.id)

                # Video moi hop le thi cong don vao lich su candidate.
                review_created_at = review.created_at or datetime.now(timezone.utc)
                histories_by_candidate_id = self._apply_candidate_rollups(
                    session=session,
                    review_id=int(review.id),
                    review_created_at=review_created_at,
                    candidate_rollups=candidate_rollups,
                )
                session.flush()
                self._record_candidate_incidents(
                    session=session,
                    review_id=int(review.id),
                    incidents=incidents,
                    histories_by_candidate_id=histories_by_candidate_id,
                )
                session.flush()
                return int(review.id)
        except (SQLAlchemyError, RuntimeError, ValueError, TypeError):
            return None

    def _parse_json_text(self, raw_value: str | None) -> dict[str, Any]:
        # Cac cot summary_json/engines_json duoc luu dang text trong SQL.
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _parse_json_list(self, raw_value: str | None) -> list[Any]:
        if not raw_value:
            return []
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def _normalize_created_at(self, created_at: datetime | None) -> str:
        if created_at is None:
            return ""
        return created_at.isoformat()

    def _review_to_payload(self, session, review) -> dict[str, Any]:
        incidents = [
            incident.to_payload()
            for incident in session.scalars(
                select(ReviewIncident).where(ReviewIncident.review_id == review.id).order_by(ReviewIncident.id.asc())
            ).all()
        ]
        summary_payload = self._parse_json_text(review.summary_json)
        video_name = Path(review.video_path).name if getattr(review, "video_path", None) else ""
        return {
            "status": review.status,
            "analysis_mode": review.analysis_mode,
            "video_path": review.video_path,
            "video_name": video_name,
            "summary": summary_payload,
            "students_report": summary_payload.get("students_report", []),
            "primary_candidate": summary_payload.get("primary_candidate"),
            "incidents": incidents,
            "engines": self._parse_json_text(review.engines_json),
            "message": review.message,
            "teacher_review": self._teacher_review_payload(
                decision=getattr(review, "teacher_decision", None),
                decided_at=getattr(review, "teacher_decided_at", None),
            ),
            "result_path": review.result_path,
            "created_at": self._normalize_created_at(review.created_at),
        }

    def get_latest_review_result(self) -> dict[str, Any] | None:
        # Phuc vu dashboard review panel: lay lan review moi nhat trong SQL.
        if not self.is_available() or select is None or ReviewResult is None or ReviewIncident is None:
            return None

        try:
            with db_session_manager.session_scope() as session:
                review = session.scalar(select(ReviewResult).order_by(ReviewResult.created_at.desc()))
                if review is None:
                    return None
                return self._review_to_payload(session, review)
        except (SQLAlchemyError, RuntimeError):
            return None

    def get_latest_review_result_for_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        # Phuc vu luong mo lan hau kiem moi nhat khi click vao mot thi sinh tren dashboard.
        if not self.is_available() or select is None or ReviewResult is None:
            return None

        normalized_candidate_id = self._normalize_candidate_id(candidate_id)
        if not normalized_candidate_id:
            return None

        try:
            with db_session_manager.session_scope() as session:
                review = None

                if CandidateHistory is not None:
                    history = session.scalar(
                        select(CandidateHistory).where(CandidateHistory.candidate_id == normalized_candidate_id)
                    )
                    if history is not None and history.last_review_id is not None:
                        review = session.scalar(
                            select(ReviewResult).where(ReviewResult.id == int(history.last_review_id))
                        )

                if review is None and ReviewIncident is not None:
                    review = session.scalar(
                        select(ReviewResult)
                        .join(ReviewIncident, ReviewIncident.review_id == ReviewResult.id)
                        .where(ReviewIncident.candidate_id == normalized_candidate_id)
                        .order_by(ReviewResult.created_at.desc(), ReviewResult.id.desc())
                        .limit(1)
                    )

                if review is None:
                    return None
                return self._review_to_payload(session, review)
        except (SQLAlchemyError, RuntimeError, ValueError, TypeError):
            return None

    def update_review_decision(
        self,
        *,
        decision: str,
        result_path: str = "",
        video_path: str = "",
    ) -> dict[str, Any] | None:
        # Teacher co the mark confirmed/dismissed cho mot lan review da luu trong DB.
        if not self.is_available() or select is None or ReviewResult is None:
            return None

        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"confirmed", "dismissed"}:
            raise ValueError("Quyet dinh khong hop le.")

        normalized_result_path = str(result_path or "").strip()
        normalized_video_path = str(video_path or "").strip()

        try:
            with db_session_manager.session_scope() as session:
                review = None
                if normalized_result_path:
                    review = session.scalar(select(ReviewResult).where(ReviewResult.result_path == normalized_result_path))
                if review is None and normalized_video_path:
                    review = session.scalar(select(ReviewResult).where(ReviewResult.video_path == normalized_video_path))
                if review is None:
                    review = session.scalar(select(ReviewResult).order_by(ReviewResult.created_at.desc()))
                if review is None:
                    return None

                decided_at = datetime.now(timezone.utc)
                review.teacher_decision = normalized_decision
                review.teacher_decided_at = decided_at
                session.flush()

                return self._teacher_review_payload(
                    decision=normalized_decision,
                    decided_at=decided_at,
                )
        except (SQLAlchemyError, RuntimeError):
            return None

    def list_recent_reviews(self, limit: int = 5) -> list[dict[str, Any]]:
        # Feed du lieu nhe cho khu recent results tren dashboard.
        if not self.is_available() or select is None or ReviewResult is None:
            return []

        try:
            with db_session_manager.session_scope() as session:
                rows = session.scalars(
                    select(ReviewResult).order_by(ReviewResult.created_at.desc()).limit(max(1, limit))
                ).all()
                return [self._review_to_payload(session, row) for row in rows]
        except (SQLAlchemyError, RuntimeError):
            return []

    def list_recent_incident_timestamps(self, hours: int = 24) -> list[str]:
        # Lay moc thoi gian incidents de ve bieu do trend tren dashboard.
        if not self.is_available() or select is None or ReviewResult is None or ReviewIncident is None:
            return []

        lookback_hours = max(1, int(hours or 24))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

        try:
            with db_session_manager.session_scope() as session:
                rows = session.execute(
                    select(ReviewResult.created_at)
                    .join(ReviewIncident, ReviewIncident.review_id == ReviewResult.id)
                    .where(ReviewResult.created_at >= cutoff)
                    .order_by(ReviewResult.created_at.asc(), ReviewIncident.id.asc())
                ).all()
                return [
                    self._normalize_created_at(created_at)
                    for (created_at,) in rows
                    if created_at is not None
                ]
        except (SQLAlchemyError, RuntimeError):
            return []

    def list_candidate_histories(self, limit: int | None = None) -> list[dict[str, Any]]:
        # Doc bang lich su tong hop theo thi sinh cho tab Students.
        if not self.is_available() or select is None or CandidateHistory is None:
            return []

        try:
            with db_session_manager.session_scope() as session:
                statement = select(CandidateHistory).order_by(
                    CandidateHistory.last_seen_at.desc(),
                    CandidateHistory.total_alerts.desc(),
                    CandidateHistory.candidate_id.asc(),
                )
                if isinstance(limit, int) and limit > 0:
                    statement = statement.limit(limit)

                rows = session.scalars(statement).all()
                candidate_verdicts: dict[str, dict[str, Any]] = {}
                if rows and ReviewResult is not None:
                    last_review_ids = sorted(
                        {int(row.last_review_id) for row in rows if row.last_review_id is not None}
                    )
                    reviews_by_id: dict[int, Any] = {}
                    if last_review_ids:
                        reviews_by_id = {
                            int(review.id): review
                            for review in session.scalars(
                                select(ReviewResult).where(ReviewResult.id.in_(last_review_ids))
                            ).all()
                        }

                    candidate_verdicts = {}
                    for row in rows:
                        if row.last_review_id is None:
                            continue
                        review = reviews_by_id.get(int(row.last_review_id))
                        if review is None:
                            continue
                        candidate_verdicts[row.candidate_id] = self._teacher_review_payload(
                            decision=getattr(review, "teacher_decision", None),
                            decided_at=getattr(review, "teacher_decided_at", None),
                        )

                return [
                    {
                        "candidate_id": row.candidate_id,
                        "name": row.name or row.candidate_id,
                        "email": row.email or "",
                        "room": row.room or "",
                        "alerts": int(row.total_alerts or 0),
                        "risk": str(row.risk or "low"),
                        "behaviors": self._normalize_behaviors(self._parse_json_list(row.behaviors_json)),
                        "review_count": int(row.total_reviews or 0),
                        "first_seen_at": self._normalize_created_at(row.first_seen_at),
                        "last_seen_at": self._normalize_created_at(row.last_seen_at),
                        "last_review_id": int(row.last_review_id) if row.last_review_id is not None else None,
                        "teacher_review": candidate_verdicts.get(
                            row.candidate_id,
                            self._teacher_review_payload(decision=None, decided_at=None),
                        ),
                    }
                    for row in rows
                ]
        except (SQLAlchemyError, RuntimeError):
            return []

    def list_candidate_incidents(self, candidate_id: str, limit: int = 100) -> list[dict[str, Any]]:
        # Doc lich su chi tiet cac incident cua mot candidate.
        if not self.is_available() or select is None or CandidateIncidentHistory is None:
            return []

        normalized_candidate_id = self._normalize_candidate_id(candidate_id)
        if not normalized_candidate_id:
            return []

        try:
            with db_session_manager.session_scope() as session:
                rows = session.scalars(
                    select(CandidateIncidentHistory)
                    .where(CandidateIncidentHistory.candidate_id == normalized_candidate_id)
                    .order_by(CandidateIncidentHistory.created_at.desc(), CandidateIncidentHistory.id.desc())
                    .limit(max(1, limit))
                ).all()
                return [row.to_payload() for row in rows]
        except (SQLAlchemyError, RuntimeError):
            return []

    def backfill_video_hashes(self) -> int:
        # Bo sung hash cho cac ban ghi cu neu truoc day chua luu video_hash.
        if not self.is_available() or select is None or UploadedVideo is None or ReviewResult is None:
            return 0

        updated_count = 0
        try:
            with db_session_manager.session_scope() as session:
                uploads = session.scalars(select(UploadedVideo)).all()
                for upload in uploads:
                    if self._normalize_video_hash(upload.content_hash):
                        continue
                    content_hash = self._compute_file_sha256(upload.file_path)
                    if not content_hash:
                        continue
                    upload.content_hash = content_hash
                    updated_count += 1

                reviews = session.scalars(select(ReviewResult)).all()
                for review in reviews:
                    if self._normalize_video_hash(review.video_hash):
                        continue
                    resolved_hash = ""
                    if review.upload_id is not None:
                        upload = session.scalar(select(UploadedVideo).where(UploadedVideo.id == review.upload_id))
                        if upload is not None:
                            resolved_hash = self._normalize_video_hash(upload.content_hash)
                    if not resolved_hash:
                        resolved_hash = self._compute_file_sha256(review.video_path)
                    if not resolved_hash:
                        continue
                    review.video_hash = resolved_hash
                    updated_count += 1
                session.flush()
        except (SQLAlchemyError, RuntimeError):
            return 0

        return updated_count

    def rebuild_candidate_histories(self) -> int:
        # Tai tao toan bo candidate_histories va candidate_incident_histories tu review_results/review_incidents.
        # Ham nay huu ich khi khoi dong app, khi doi logic, hoac khi can "repair" du lieu lich su.
        if (
            not self.is_available()
            or select is None
            or delete is None
            or ReviewResult is None
            or ReviewIncident is None
            or CandidateHistory is None
            or CandidateIncidentHistory is None
        ):
            return 0

        rebuilt_review_count = 0
        try:
            with db_session_manager.session_scope() as session:
                session.execute(delete(CandidateIncidentHistory))
                session.execute(delete(CandidateHistory))
                session.flush()

                seen_video_hashes: set[str] = set()
                reviews = session.scalars(
                    select(ReviewResult).order_by(ReviewResult.created_at.asc(), ReviewResult.id.asc())
                ).all()

                for review in reviews:
                    video_hash = self._normalize_video_hash(review.video_hash)
                    if video_hash and video_hash in seen_video_hashes:
                        continue

                    incident_rows = session.scalars(
                        select(ReviewIncident)
                        .where(ReviewIncident.review_id == review.id)
                        .order_by(ReviewIncident.id.asc())
                    ).all()
                    incidents = [incident.to_payload() for incident in incident_rows]
                    # Dung payload tam de enrich lai candidate context va xay lai rollup.
                    payload = self._build_review_payload(review, incidents)
                    enriched_incidents = self._enrich_incidents_with_candidate_context(
                        payload.get("incidents", []),
                        result_payload=payload,
                    )
                    payload["incidents"] = enriched_incidents
                    for incident_row, enriched_incident in zip(incident_rows, enriched_incidents):
                        incident_row.candidate_id = self._normalize_candidate_id(enriched_incident.get("candidate_id")) or None
                        incident_row.candidate_name = str(enriched_incident.get("candidate_name") or "") or None
                        incident_row.candidate_email = str(enriched_incident.get("candidate_email") or "") or None
                        incident_row.candidate_room = str(enriched_incident.get("candidate_room") or "") or None
                    histories_by_candidate_id = self._apply_candidate_rollups(
                        session=session,
                        review_id=int(review.id),
                        review_created_at=review.created_at or datetime.now(timezone.utc),
                        candidate_rollups=self._build_candidate_rollups(payload),
                    )
                    session.flush()
                    self._record_candidate_incidents(
                        session=session,
                        review_id=int(review.id),
                        incidents=enriched_incidents,
                        histories_by_candidate_id=histories_by_candidate_id,
                    )
                    if video_hash:
                        seen_video_hashes.add(video_hash)
                    rebuilt_review_count += 1

                self._sync_candidate_history_last_review_refs(session=session)
                session.flush()
        except (SQLAlchemyError, RuntimeError, ValueError, TypeError):
            return 0

        return rebuilt_review_count

    def sync_candidate_history_last_review_ids(self) -> int:
        if (
            not self.is_available()
            or select is None
            or CandidateHistory is None
            or ReviewIncident is None
            or ReviewResult is None
        ):
            return 0

        try:
            with db_session_manager.session_scope() as session:
                updated_count = self._sync_candidate_history_last_review_refs(session=session)
                session.flush()
                return updated_count
        except (SQLAlchemyError, RuntimeError, ValueError, TypeError):
            return 0

    def backfill_reviews_from_results_dir(self, results_dir: str | Path = "results") -> int:
        if not self.is_available() or select is None or ReviewResult is None:
            return 0

        target_dir = Path(results_dir)
        if not target_dir.exists():
            return 0

        imported_count = 0
        for result_file in sorted(target_dir.glob("*.json"), key=lambda item: item.stat().st_mtime):
            try:
                payload = json.loads(result_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue

            result_path = str(payload.get("result_path") or result_file.as_posix())
            video_path = str(payload.get("video_path") or "")
            try:
                with db_session_manager.session_scope() as session:
                    exists = self._review_exists_for_result(session, result_path=result_path, video_path=video_path)
                if exists:
                    continue
            except (SQLAlchemyError, RuntimeError):
                continue

            payload["result_path"] = result_path
            review_id = self.save_review_result(payload)
            if review_id is not None:
                imported_count += 1

        return imported_count


sql_storage_service = SQLStorageService()
