import csv
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from io import StringIO
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from app.services.dashboard_service import get_dashboard_payload
from app.services.candidate_registry_service import candidate_registry_service
from app.services.detection_service import DetectionService
from app.services.settings_service import DEFAULT_AI_SETTINGS, get_ai_settings, settings_service
from app.services.sql_storage_service import sql_storage_service
from app.services.video_service import VideoService


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
video_service = VideoService()
detection_service = DetectionService()
VALID_TABS = {"overview", "review", "students", "settings"}
DEFAULT_TAB = "settings"
PAGE_TITLES = {
    "overview": "System Oversight",
    "review": "Chi tiết Hậu Kiểm",
    "students": "Danh sách Thí sinh & Vi phạm",
    "settings": "Cấu hình Hệ thống AI",
}
VIETNAM_TZ = timezone(timedelta(hours=7))


class SettingsPayload(BaseModel):
    confidence_threshold: float
    phone_conf_threshold: float
    extraction_interval_seconds: float
    behavior_threshold: float
    enable_gaze_alerts: bool
    enable_cell_phone_alerts: bool
    enable_face_missing_alerts: bool
    enable_multiple_people_alerts: bool


class ReviewDecisionPayload(BaseModel):
    decision: str
    result_path: str | None = None
    video_path: str | None = None


def _risk_priority(risk: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(risk or "low"), 1)


def _pick_primary_candidate_from_students(students: list[dict]) -> dict | None:
    if not students:
        return None

    def score(item: dict) -> tuple[int, int, int]:
        candidate_id = str(item.get("candidate_id") or "")
        known_flag = 1 if candidate_id and candidate_id != "UNKNOWN" else 0
        alerts = int(item.get("alerts") or 0)
        risk_weight = _risk_priority(str(item.get("risk") or "low"))
        return (known_flag, alerts, risk_weight)

    top = max(students, key=score)
    return {
        "candidate_id": str(top.get("candidate_id") or "UNKNOWN"),
        "name": str(top.get("name") or "Unknown Candidate"),
        "email": str(top.get("email") or ""),
        "room": str(top.get("room") or ""),
        "alerts": int(top.get("alerts") or 0),
        "risk": str(top.get("risk") or "low"),
        "behaviors": list(top.get("behaviors") or []),
    }


def _student_identity_key(student: dict) -> str:
    candidate_id = str(student.get("candidate_id") or "").strip()
    if candidate_id:
        return candidate_id
    email = str(student.get("email") or "").strip().lower()
    if email:
        return email
    return str(student.get("name") or "").strip().lower()


def _summarize_batch_names(names: list[str], limit: int = 3) -> str:
    visible_names = [str(name).strip() for name in names if str(name).strip()][:limit]
    if not visible_names:
        return ""
    remainder = len(names) - len(visible_names)
    summary = ", ".join(visible_names)
    if remainder > 0:
        summary = f"{summary} (+{remainder})"
    return summary


def _build_batch_status(success_count: int, failure_count: int, warning_count: int = 0) -> str:
    if success_count > 0 and failure_count == 0 and warning_count == 0:
        return "success"
    if success_count > 0 or warning_count > 0:
        return "warning"
    return "error"


def _build_upload_batch_message(successful_uploads: list[dict], upload_failures: list[str]) -> str:
    success_count = len(successful_uploads)
    failure_count = len(upload_failures)
    successful_names = [str(item.get("original_filename") or "") for item in successful_uploads]
    failure_detail = _summarize_batch_names(upload_failures, limit=2)

    if success_count == 0:
        message = "Khong co video nao duoc tai len."
        if failure_detail:
            message = f"{message} Chi tiet: {failure_detail}."
        return message

    summary = _summarize_batch_names(successful_names)
    message = f"Da tai len {success_count} video"
    if summary:
        message = f"{message}: {summary}."
    else:
        message = f"{message}."
    if failure_count > 0:
        message = f"{message} {failure_count} video tai len that bai."
        if failure_detail:
            message = f"{message} Chi tiet: {failure_detail}."
    return message


def _build_detection_batch_message(
    detection_successes: list[str],
    detection_warnings: list[str],
    detection_failures: list[str],
) -> str:
    success_count = len(detection_successes)
    warning_count = len(detection_warnings)
    failure_count = len(detection_failures)
    total_count = success_count + warning_count + failure_count

    if total_count == 0:
        return "Chua co video nao duoc dua vao hau kiem."

    parts: list[str] = [f"Da xu ly {total_count} video"]
    if success_count > 0:
        parts.append(f"thanh cong {success_count}")
    if warning_count > 0:
        parts.append(f"canh bao {warning_count}")
    if failure_count > 0:
        parts.append(f"that bai {failure_count}")

    detail_sources = detection_failures or detection_warnings
    detail = _summarize_batch_names(detail_sources, limit=2)
    message = ", ".join(parts) + "."
    if detail:
        message = f"{message} Chi tiet: {detail}."
    return message


def _merge_students_for_dashboard(latest_students: list[dict], historical_students: list[dict]) -> list[dict]:
    merged: list[dict] = []
    index_by_key: dict[str, int] = {}

    for student in latest_students:
        row = dict(student)
        key = _student_identity_key(row)
        if not key or key in index_by_key:
            continue
        index_by_key[key] = len(merged)
        merged.append(row)

    for student in historical_students:
        row = dict(student)
        key = _student_identity_key(row)
        if not key:
            continue
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(merged)
            merged.append(row)
            continue

        existing = merged[existing_index]
        if "teacher_review" not in existing and "teacher_review" in row:
            existing["teacher_review"] = row["teacher_review"]
        if not existing.get("room") and row.get("room"):
            existing["room"] = row["room"]
        if not existing.get("email") and row.get("email"):
            existing["email"] = row["email"]

    return merged


def _candidate_profile_key(value: object) -> str:
    return str(value or "").strip().casefold()


def _candidate_profiles_by_id(candidates: list[dict]) -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = _candidate_profile_key(candidate.get("candidate_id"))
        if key:
            profiles[key] = candidate
    return profiles


def _apply_candidate_profile_to_student(student: dict, profiles_by_id: dict[str, dict]) -> dict:
    row = dict(student)
    profile = profiles_by_id.get(_candidate_profile_key(row.get("candidate_id")))
    if profile is None:
        return row

    candidate_id = str(profile.get("candidate_id") or row.get("candidate_id") or "").strip()
    row["candidate_id"] = candidate_id
    row["name"] = str(profile.get("name") or row.get("name") or candidate_id).strip()
    row["email"] = str(profile.get("email") or "").strip()
    row["room"] = str(profile.get("room") or "").strip()
    return row


def _apply_candidate_profiles_to_students(students: list[dict], candidates: list[dict]) -> list[dict]:
    profiles_by_id = _candidate_profiles_by_id(candidates)
    if not profiles_by_id:
        return [dict(student) for student in students]
    return [_apply_candidate_profile_to_student(student, profiles_by_id) for student in students]


def _avatar_from_candidate_name(name: object, fallback: str = "UC") -> str:
    parts = [part[0] for part in str(name or "").split() if part]
    return "".join(parts[:2]).upper() or fallback


def _apply_candidate_profile_to_candidate_payload(candidate: dict | None, profiles_by_id: dict[str, dict]) -> dict | None:
    if not isinstance(candidate, dict):
        return candidate

    row = dict(candidate)
    profile = profiles_by_id.get(_candidate_profile_key(row.get("candidate_id")))
    if profile is None:
        return row

    candidate_id = str(profile.get("candidate_id") or row.get("candidate_id") or "").strip()
    row["candidate_id"] = candidate_id
    row["name"] = str(profile.get("name") or row.get("name") or candidate_id).strip()
    row["email"] = str(profile.get("email") or "").strip()
    row["room"] = str(profile.get("room") or "").strip()
    if "avatar" in row:
        row["avatar"] = _avatar_from_candidate_name(row.get("name"), fallback=str(row.get("avatar") or "UC"))
    return row


def _apply_candidate_profile_to_incident(incident: dict, profiles_by_id: dict[str, dict]) -> dict:
    row = dict(incident)
    profile = profiles_by_id.get(_candidate_profile_key(row.get("candidate_id")))
    if profile is None:
        return row

    candidate_id = str(profile.get("candidate_id") or row.get("candidate_id") or "").strip()
    row["candidate_id"] = candidate_id
    row["candidate_name"] = str(profile.get("name") or row.get("candidate_name") or candidate_id).strip()
    row["candidate_email"] = str(profile.get("email") or "").strip()
    row["candidate_room"] = str(profile.get("room") or "").strip()
    return row


def _apply_candidate_profiles_to_review(review: dict, candidates: list[dict]) -> dict:
    profiles_by_id = _candidate_profiles_by_id(candidates)
    if not profiles_by_id:
        return dict(review)

    row = dict(review)
    summary = dict(row.get("summary") or {})
    students = row.get("students_report") or summary.get("students_report") or []
    if isinstance(students, list):
        students = _apply_candidate_profiles_to_students(students, candidates)
        row["students_report"] = students
        summary["students_report"] = students

    primary_candidate = _apply_candidate_profile_to_candidate_payload(
        row.get("primary_candidate") or summary.get("primary_candidate"),
        profiles_by_id,
    )
    row["primary_candidate"] = primary_candidate
    summary["primary_candidate"] = primary_candidate
    row["summary"] = summary

    incidents = row.get("incidents") or []
    if isinstance(incidents, list):
        row["incidents"] = [
            _apply_candidate_profile_to_incident(incident, profiles_by_id)
            for incident in incidents
            if isinstance(incident, dict)
        ]

    review_candidate = _apply_candidate_profile_to_candidate_payload(row.get("review_candidate"), profiles_by_id)
    if isinstance(review_candidate, dict):
        review_candidate["avatar"] = _avatar_from_candidate_name(
            review_candidate.get("name"),
            fallback=str(review_candidate.get("avatar") or "UC"),
        )
        row["review_candidate"] = review_candidate
    return row


def _apply_candidate_profiles_to_reviews(reviews: list[dict], candidates: list[dict]) -> list[dict]:
    return [_apply_candidate_profiles_to_review(review, candidates) for review in reviews]


def _room_sort_key(room_name: str) -> tuple[int, str]:
    normalized = str(room_name or "").strip()
    if not normalized:
        return (1, "")
    return (0, normalized.casefold())


def _build_room_cards(students: list[dict]) -> list[dict[str, object]]:
    rooms: dict[str, dict[str, object]] = {}

    for student in students:
        if not isinstance(student, dict):
            continue
        room_name = str(student.get("room") or "").strip()
        if not room_name:
            continue

        card = rooms.get(room_name)
        if card is None:
            card = {
                "room_name": room_name,
                "room_subtitle": "Du lieu hau kiem hien co",
                "student_count": 0,
                "alert_count": 0,
                "high_risk_count": 0,
                "confirmed_count": 0,
                "review_count": 0,
            }
            rooms[room_name] = card

        card["student_count"] = int(card["student_count"]) + 1
        card["alert_count"] = int(card["alert_count"]) + int(student.get("alerts") or 0)
        card["review_count"] = int(card["review_count"]) + int(student.get("review_count") or 0)

        if str(student.get("risk") or "").strip().lower() == "high":
            card["high_risk_count"] = int(card["high_risk_count"]) + 1

        teacher_review = student.get("teacher_review") if isinstance(student.get("teacher_review"), dict) else {}
        if str(teacher_review.get("status") or "").strip().lower() == "confirmed":
            card["confirmed_count"] = int(card["confirmed_count"]) + 1

    room_cards: list[dict[str, object]] = []
    for room_name in sorted(rooms.keys(), key=_room_sort_key):
        card = rooms[room_name]
        student_count = int(card["student_count"])
        alert_count = int(card["alert_count"])
        high_risk_count = int(card["high_risk_count"])
        confirmed_count = int(card["confirmed_count"])
        review_count = int(card["review_count"])

        status = "processing"
        badge_label = "Processing"
        preview_variant = "blue"
        status_copy = "Dang co du lieu hau kiem"
        status_copy_class = "warning-copy"
        signal_count = min(4, max(1, student_count))
        card_class = ""

        if confirmed_count > 0 or high_risk_count > 0:
            status = "suspended"
            badge_label = "Suspended"
            preview_variant = "danger"
            status_copy = (
                f"{confirmed_count} ket luan gian lan"
                if confirmed_count > 0
                else f"{high_risk_count} thi sinh rui ro cao"
            )
            status_copy_class = "danger-copy"
            signal_count = 4
            card_class = "danger"
        elif alert_count <= 0:
            status = "completed"
            badge_label = "Completed"
            preview_variant = "neutral"
            status_copy = "Khong co canh bao"
            status_copy_class = "safe-copy"
            signal_count = max(1, min(2, student_count))
        elif alert_count >= 3:
            status = "processing"
            badge_label = "Processing"
            preview_variant = "gold"
            status_copy = f"{alert_count} canh bao"
            status_copy_class = "warning-copy"
            signal_count = min(4, max(2, alert_count))
        else:
            status_copy = f"{alert_count} canh bao"

        room_cards.append(
            {
                "room_name": room_name,
                "room_subtitle": f"{student_count} thi sinh, {review_count} lan hau kiem",
                "room_count_label": f"{student_count} thi sinh",
                "status": status,
                "badge_label": badge_label,
                "preview_variant": preview_variant,
                "status_copy": status_copy,
                "status_copy_class": status_copy_class,
                "signal_count": signal_count,
                "card_class": card_class,
                "href": f"/?{urlencode({'tab': 'students', 'students_room': room_name})}",
            }
        )

    return room_cards


def _risk_label_vi(risk: str) -> str:
    return {"high": "CAO", "medium": "TRUNG BINH", "low": "THAP"}.get(str(risk or "low"), "THAP")


def _risk_badge_vi(risk: str) -> str:
    return {"high": "Rat cao", "medium": "Trung binh", "low": "Thap"}.get(str(risk or "low"), "Thap")


def _teacher_verdict_payload(status: str | None) -> dict[str, str | None]:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"confirmed", "dismissed"}:
        normalized_status = "pending"
    label_map = {
        "confirmed": "Gian lận",
        "dismissed": "Không gian lận",
        "pending": "Chưa kết luận",
    }
    return {"status": normalized_status, "label": label_map[normalized_status], "decided_at": None}


def _teacher_verdict_label_for_export(student: dict) -> str:
    teacher_review = student.get("teacher_review") if isinstance(student, dict) else {}
    status = str((teacher_review or {}).get("status") or "").strip().lower()
    return {
        "confirmed": "Gian lan",
        "dismissed": "Khong gian lan",
        "pending": "Chua ket luan",
    }.get(status, "Chua ket luan")


def _format_csv_datetime(raw_value: str | None) -> str:
    return _format_vietnam_datetime(raw_value, empty_label="")


def _review_device_status(latest_review: dict) -> str:
    engines = latest_review.get("engines", {}) if isinstance(latest_review, dict) else {}
    face_engine = engines.get("face_recognition", {}) if isinstance(engines, dict) else {}
    if face_engine.get("enabled"):
        return "Da xac minh"
    return "Cho xac minh"


def _format_mmss(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes = total_seconds // 60
    remain_seconds = total_seconds % 60
    return f"{minutes:02d}:{remain_seconds:02d}"


def _parse_iso_datetime(raw_value: str | None) -> datetime | None:
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return None
    try:
        parsed = datetime.fromisoformat(raw_text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_vietnam_datetime(raw_value: str | None, empty_label: str = "Chua co moc xu ly.") -> str:
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return empty_label

    parsed = _parse_iso_datetime(raw_text)
    if parsed is None:
        return raw_text

    return parsed.astimezone(VIETNAM_TZ).strftime("%d/%m/%Y %H:%M:%S")


def _coerce_float(raw_value) -> float | None:
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    return None


def _incident_category_label(incident: dict) -> str:
    event_type = str(incident.get("event_type") or "").strip().lower()
    if event_type in {"cell_phone", "hand_phone"}:
        return "Dung dien thoai"
    if event_type == "multiple_people":
        return "Them nguoi"
    if event_type == "face_missing":
        return "Roi khung hinh"
    if event_type == "gaze":
        return "Liec mat bat thuong"
    if event_type == "head_pose":
        return "Quay dau bat thuong"
    if event_type == "behavior_model":
        return "Mo hinh hanh vi nghi van"

    label = str(incident.get("label") or "").strip()
    return label or "Khac"


def build_anomaly_type_metrics(reviews: list[dict], hours: int = 24, limit_rows: int = 3) -> list[dict[str, str | int]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours or 24)))
    counter: Counter[str] = Counter()
    total_incidents = 0

    for review in reviews:
        created_at = _parse_iso_datetime(review.get("created_at"))
        if created_at is not None and created_at < cutoff:
            continue
        for incident in review.get("incidents") or []:
            if not isinstance(incident, dict):
                continue
            label = _incident_category_label(incident)
            counter[label] += 1
            total_incidents += 1

    if total_incidents <= 0:
        return [{"label": "Chua co canh bao", "percentage_label": "0%", "count": 0}]

    rows: list[dict[str, str | int]] = []
    for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[: max(1, limit_rows)]:
        percentage = round((count / total_incidents) * 100)
        rows.append(
            {
                "label": label,
                "percentage_label": f"{percentage}%",
                "count": int(count),
            }
        )
    return rows


def build_processing_power_bars(reviews: list[dict], limit: int = 10) -> list[dict[str, str | int | float]]:
    points: list[dict[str, float | str]] = []

    for review in reviews:
        summary = review.get("summary", {}) if isinstance(review, dict) else {}
        fps_value = _coerce_float(summary.get("effective_processing_fps"))
        latency_ms = _coerce_float(summary.get("avg_frame_processing_ms"))
        if latency_ms is None:
            latency_ms = _coerce_float(summary.get("yolo_avg_pipeline_ms"))

        if fps_value is None and latency_ms and latency_ms > 0:
            fps_value = round(1000.0 / latency_ms, 2)

        if fps_value is None or fps_value <= 0:
            continue

        created_at = _parse_iso_datetime(review.get("created_at"))
        label = created_at.astimezone(VIETNAM_TZ).strftime("%H:%M") if created_at is not None else "N/A"
        points.append(
            {
                "value": fps_value,
                "label": label,
                "tooltip": f"{label}: {fps_value:.2f} FPS",
            }
        )

    if not points:
        return []

    points = points[: max(1, limit)]
    points.reverse()
    max_value = max(float(point["value"]) for point in points)
    bars: list[dict[str, str | int | float]] = []
    for point in points:
        value = float(point["value"])
        height = 0 if max_value <= 0 else max(12, int(round((value / max_value) * 100)))
        bars.append(
            {
                "value": value,
                "height": height,
                "label": str(point["label"]),
                "tooltip": str(point["tooltip"]),
            }
        )
    return bars


def build_violation_trend(
    latest_review: dict,
    trend_timestamps: list[str] | None = None,
    bucket_count: int = 6,
) -> list[dict[str, int | str | bool]]:
    if trend_timestamps is not None:
        window_now = datetime.now(VIETNAM_TZ)
        aligned_hour = (window_now.hour // 4) * 4
        current_bucket_start = window_now.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)
        window_start = current_bucket_start - timedelta(hours=4 * max(0, bucket_count - 1))
        bucket_span_seconds = float(4 * 60 * 60)
        counts = [0 for _ in range(bucket_count)]

        for raw_value in trend_timestamps:
            raw_text = str(raw_value or "").strip()
            if not raw_text:
                continue
            try:
                parsed = datetime.fromisoformat(raw_text.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            local_time = parsed.astimezone(VIETNAM_TZ)
            if local_time < window_start or local_time > window_now:
                continue
            elapsed_seconds = (local_time - window_start).total_seconds()
            bucket_index = min(bucket_count - 1, int(elapsed_seconds / max(1.0, bucket_span_seconds)))
            counts[bucket_index] += 1

        labels = [
            (window_start + timedelta(seconds=bucket_span_seconds * index)).strftime("%H:%M")
            for index in range(bucket_count)
        ]

        max_count = max(counts) if counts else 0
        peak_index = counts.index(max_count) if max_count > 0 else -1

        trend_points: list[dict[str, int | str | bool]] = []
        for index, count in enumerate(counts):
            if max_count <= 0 or count <= 0:
                height = 0
            else:
                height = max(8, int(round((count / max_count) * 100)))
            trend_points.append(
                {
                    "label": labels[index],
                    "count": count,
                    "height": height,
                    "highlight": max_count > 0 and index == peak_index,
                    "current": index == bucket_count - 1,
                    "tooltip": f"{labels[index]}: {count} sự cố",
                }
            )
        return trend_points

    incidents = latest_review.get("incidents", []) if isinstance(latest_review, dict) else []
    summary = latest_review.get("summary", {}) if isinstance(latest_review, dict) else {}
    duration_seconds = float(summary.get("duration_seconds") or 0.0)

    if duration_seconds <= 0 and incidents:
        duration_seconds = max(float(item.get("time_seconds") or 0.0) for item in incidents)

    counts = [0 for _ in range(bucket_count)]
    if duration_seconds > 0:
        for incident in incidents:
            timestamp_seconds = float(incident.get("time_seconds") or 0.0)
            ratio = min(1.0, max(0.0, timestamp_seconds / duration_seconds))
            bucket_index = min(bucket_count - 1, int(ratio * bucket_count))
            counts[bucket_index] += 1

    if duration_seconds > 0 and bucket_count > 1:
        step = duration_seconds / (bucket_count - 1)
        labels = [_format_mmss(step * index) for index in range(bucket_count - 1)] + ["Hiện tại"]
    else:
        labels = ["08:00", "12:00", "14:00", "16:00", "20:00", "Hiện tại"][:bucket_count]

    max_count = max(counts) if counts else 0
    peak_index = counts.index(max_count) if max_count > 0 else -1

    trend_points: list[dict[str, int | str | bool]] = []
    for index, count in enumerate(counts):
        if max_count <= 0:
            height = 0
        elif count <= 0:
            height = 0
        else:
            height = max(8, int(round((count / max_count) * 100)))
        trend_points.append(
            {
                "label": labels[index],
                "count": count,
                "height": height,
                "highlight": max_count > 0 and index == peak_index,
                "current": index == bucket_count - 1,
                "tooltip": f"{labels[index]}: {count} sự cố",
            }
        )
    return trend_points


def build_performance_metrics(latest_review: dict) -> dict:
    summary = latest_review.get("summary", {}) if isinstance(latest_review, dict) else {}
    latency_ms = summary.get("avg_frame_processing_ms")
    if latency_ms is None:
        latency_ms = summary.get("yolo_avg_pipeline_ms")

    gpu_percent = summary.get("gpu_memory_peak_percent")
    compute_backend = str(summary.get("compute_backend") or "cpu").lower()

    if isinstance(latency_ms, (int, float)):
        latency_label = f"~{latency_ms:.0f}ms"
        latency_seconds_label = f"{(float(latency_ms) / 1000.0):.2f}s"
    else:
        latency_label = "--"
        latency_seconds_label = "--"

    if isinstance(gpu_percent, (int, float)):
        gpu_label = f"{gpu_percent:.1f}%"
    elif compute_backend == "cpu":
        gpu_label = "0% (CPU)"
    else:
        gpu_label = "--"

    processing_fps = summary.get("effective_processing_fps")
    if isinstance(processing_fps, (int, float)):
        note = f"Toc do xu ly thuc te: {processing_fps:.1f} FPS"
    else:
        note = "Se cap nhat sau khi chay hau kiem video."

    return {
        "latency_label": latency_label,
        "latency_seconds_label": latency_seconds_label,
        "gpu_label": gpu_label,
        "note": note,
    }


def build_system_status(latest_review: dict, performance_metrics: dict[str, str]) -> dict[str, str]:
    summary = latest_review.get("summary", {}) if isinstance(latest_review, dict) else {}
    engines = latest_review.get("engines", {}) if isinstance(latest_review, dict) else {}
    face_engine = engines.get("face_recognition", {}) if isinstance(engines, dict) else {}

    effective_fps = summary.get("effective_processing_fps")
    if isinstance(effective_fps, (int, float)):
        frame_fps_label = f"{effective_fps:.1f} FPS"
    else:
        frame_fps_label = "--"

    candidate_count = int(face_engine.get("candidate_count") or 0) if face_engine.get("enabled") else 0
    face_engine_label = f"Bật ({candidate_count} hồ sơ)" if face_engine.get("enabled") else "Tắt"
    status_value = str(latest_review.get("status") or "idle")
    state_label_map = {
        "completed": "System Online",
        "idle": "Chưa có dữ liệu",
        "skipped": "Thiếu dependency",
        "error": "Có lỗi xử lý",
    }
    state_label = state_label_map.get(status_value, "System Online")

    created_at = latest_review.get("created_at")
    updated_label = "Chưa có mốc xử lý."
    if created_at:
        updated_label = f"Cập nhật: {_format_vietnam_datetime(created_at)}"

    return {
        "latency_label": performance_metrics.get("latency_label", "--"),
        "frame_fps_label": frame_fps_label,
        "face_engine_label": face_engine_label,
        "state_label": state_label,
        "updated_label": updated_label,
    }


def _empty_review_payload() -> dict[str, object]:
    return {
        "status": "idle",
        "analysis_mode": "n/a",
        "video_name": None,
        "video_url": None,
        "video_path": None,
        "result_path": None,
        "summary": {"total_violations": 0, "reviewed_frames": 0},
        "incidents": [],
        "students_report": [],
        "primary_candidate": None,
        "engines": {},
        "teacher_review": {
            "status": "pending",
            "label": "Chua quyet dinh",
            "decided_at": None,
        },
        "message": "Chua co du lieu hau kiem.",
        "created_at": None,
        "created_at_label": "Chua co moc xu ly.",
        "incident_count": 0,
        "review_candidate": None,
        "review_risk_message": "Chua ghi nhan su co trong lan hau kiem gan nhat.",
    }

def _format_review_created_at_label(created_at: str | None) -> str:
    return _format_vietnam_datetime(created_at)


def _hydrate_review_payload(source: dict | None = None) -> dict:
    latest_review = _empty_review_payload()
    if isinstance(source, dict):
        latest_review["status"] = source.get("status", latest_review["status"])
        latest_review["analysis_mode"] = source.get("analysis_mode", latest_review["analysis_mode"])
        latest_review["summary"] = source.get("summary", latest_review["summary"])
        latest_review["incidents"] = list(source.get("incidents", []))
        latest_review["students_report"] = list(
            source.get("students_report", latest_review["summary"].get("students_report", []))
        )
        latest_review["engines"] = source.get("engines", {})
        latest_review["teacher_review"] = source.get("teacher_review", latest_review["teacher_review"])
        latest_review["primary_candidate"] = source.get(
            "primary_candidate",
            latest_review["summary"].get("primary_candidate"),
        )
        latest_review["message"] = source.get("message", latest_review["message"])
        latest_review["created_at"] = source.get("created_at")
        latest_review["result_path"] = source.get("result_path")
        latest_review["video_path"] = source.get("video_path")
        latest_review["video_name"] = source.get("video_name")
        latest_review["video_url"] = source.get("video_url")

    if latest_review["primary_candidate"] is None:
        latest_review["primary_candidate"] = _pick_primary_candidate_from_students(latest_review["students_report"])

    video_path = latest_review.get("video_path")
    if video_path:
        video_file = Path(str(video_path))
        latest_review["video_name"] = latest_review.get("video_name") or video_file.name
        if video_file.exists():
            latest_review["video_url"] = f"/uploads/{video_file.name}"

    review_candidate = latest_review.get("primary_candidate") or {
        "candidate_id": "UNKNOWN",
        "name": "Unknown Candidate",
        "email": "",
        "room": "",
        "alerts": 0,
        "risk": "low",
        "behaviors": [],
    }
    review_candidate = dict(review_candidate)
    review_candidate["risk_label"] = _risk_label_vi(str(review_candidate.get("risk") or "low"))
    review_candidate["device_status"] = _review_device_status(latest_review)
    review_candidate["behaviors"] = list(review_candidate.get("behaviors") or [])
    review_candidate["avatar"] = "".join(
        [part[0] for part in str(review_candidate.get("name") or "UC").split() if part][:2]
    ).upper() or "UC"

    incident_count = len(latest_review.get("incidents", []))
    latest_review["incident_count"] = incident_count
    latest_review["review_candidate"] = review_candidate
    latest_review["review_risk_message"] = (
        f"He thong da ghi nhan {incident_count} su co trong lan hau kiem nay."
        if incident_count > 0
        else "Chua ghi nhan su co trong lan hau kiem nay."
    )
    latest_review["created_at_label"] = _format_review_created_at_label(latest_review.get("created_at"))
    return latest_review


def build_recent_review_payloads(recent_uploads: list[dict], limit: int = 5) -> list[dict]:
    raw_results = sql_storage_service.list_recent_reviews(limit=limit) or detection_service.list_result_payloads(limit=limit)
    if raw_results:
        return [_hydrate_review_payload(item) for item in raw_results]

    placeholders: list[dict] = []
    for upload in recent_uploads[: max(1, limit)]:
        filename = str(upload.get("filename") or "")
        placeholders.append(
            _hydrate_review_payload(
                {
                    "video_name": filename,
                    "video_url": f"/uploads/{filename}",
                    "video_path": str(video_service.build_upload_path(filename)) if filename else None,
                    "message": "Video da tai len, chua co ket qua phan tich.",
                }
            )
        )
    return placeholders


def build_latest_review_payload(recent_uploads: list[dict], recent_reviews: list[dict] | None = None) -> dict:
    if recent_reviews:
        return dict(recent_reviews[0])
    return _hydrate_review_payload()


def build_dashboard_context(request: Request) -> dict:
    selected_tab = request.query_params.get("tab", DEFAULT_TAB)
    if selected_tab not in VALID_TABS:
        selected_tab = DEFAULT_TAB
    review_candidate_id = str(request.query_params.get("review_candidate_id") or "").strip()

    upload_status = request.query_params.get("upload_status")
    upload_message = request.query_params.get("upload_message")
    upload_feedback = None
    if upload_status and upload_message:
        upload_feedback = {"status": upload_status, "message": upload_message}

    detection_status = request.query_params.get("detection_status")
    detection_message = request.query_params.get("detection_message")
    detection_feedback = None
    if detection_status and detection_message:
        detection_feedback = {"status": detection_status, "message": detection_message}

    candidate_status = request.query_params.get("candidate_status")
    candidate_message = request.query_params.get("candidate_message")
    candidate_feedback = None
    if candidate_status and candidate_message:
        candidate_feedback = {"status": candidate_status, "message": candidate_message}

    try:
        face_candidates = candidate_registry_service.list_candidates()
    except ValueError as exc:
        face_candidates = []
        candidate_feedback = {
            "status": "error",
            "message": str(exc),
        }

    recent_uploads = video_service.list_uploads()
    recent_results = _apply_candidate_profiles_to_reviews(
        build_recent_review_payloads(recent_uploads, limit=8),
        face_candidates,
    )
    analytics_reviews = _apply_candidate_profiles_to_reviews(
        build_recent_review_payloads(recent_uploads, limit=50),
        face_candidates,
    )
    latest_review = build_latest_review_payload(recent_uploads, recent_results)
    if review_candidate_id:
        matching_review = next(
            (
                review
                for review in recent_results
                if review_candidate_id == str(review.get("review_candidate", {}).get("candidate_id") or "").strip()
                or review_candidate_id == str(review.get("primary_candidate", {}).get("candidate_id") or "").strip()
                or any(
                    review_candidate_id == str(item.get("candidate_id") or "").strip()
                    for item in (review.get("students_report") or [])
                    if isinstance(item, dict)
                )
                or any(
                    review_candidate_id == str(item.get("candidate_id") or "").strip()
                    for item in (review.get("incidents") or [])
                    if isinstance(item, dict)
                )
            ),
            None,
        )
        if matching_review is None:
            raw_review = sql_storage_service.get_latest_review_result_for_candidate(review_candidate_id)
            if raw_review is None:
                raw_review = detection_service.get_latest_result_payload_for_candidate(review_candidate_id)
            if raw_review is not None:
                matching_review = _apply_candidate_profiles_to_review(
                    _hydrate_review_payload(raw_review),
                    face_candidates,
                )
                if not any(
                    (
                        str(item.get("result_path") or "").strip() == str(matching_review.get("result_path") or "").strip()
                        and str(item.get("video_path") or "").strip() == str(matching_review.get("video_path") or "").strip()
                    )
                    for item in recent_results
                ):
                    recent_results.insert(0, matching_review)
        if matching_review is not None:
            latest_review = dict(matching_review)
    latest_review = _apply_candidate_profiles_to_review(latest_review, face_candidates)
    ai_settings = get_ai_settings()

    historical_students = sql_storage_service.list_candidate_histories(limit=250)
    if not historical_students:
        historical_students = detection_service.list_historical_students()

    dashboard_payload = get_dashboard_payload()
    latest_students_report = latest_review.get("students_report", [])
    if latest_students_report:
        high_risk = len([item for item in latest_students_report if item.get("risk") == "high"])
        dashboard_payload["overview"]["active_sessions"] = len(latest_students_report)
        dashboard_payload["overview"]["integrity_score"] = f"{max(0.0, 100.0 - (high_risk * 8.0)):.1f}%"
    source_students = _merge_students_for_dashboard(latest_students_report, historical_students)
    source_students = _apply_candidate_profiles_to_students(source_students, face_candidates)
    dashboard_payload["students"] = [dict(student) for student in source_students]
    student_items = dashboard_payload.get("students", [])
    room_cards = _build_room_cards(student_items if isinstance(student_items, list) else [])
    students_total = len(student_items)
    students_high_risk = len([item for item in student_items if str(item.get("risk") or "") == "high"])
    dashboard_payload["overview"]["rooms_online"] = len(room_cards)

    review_candidate = dict(latest_review.get("review_candidate") or {})
    review_risk_message = str(latest_review.get("review_risk_message") or "Chua ghi nhan su co trong lan hau kiem nay.")
    performance_metrics = build_performance_metrics(latest_review)
    anomaly_type_metrics = build_anomaly_type_metrics(analytics_reviews, hours=24, limit_rows=3)
    processing_power_bars = build_processing_power_bars(analytics_reviews, limit=10)
    recent_incident_timestamps = sql_storage_service.list_recent_incident_timestamps(hours=24)
    violation_trend = build_violation_trend(
        latest_review,
        trend_timestamps=recent_incident_timestamps,
    )
    system_status = build_system_status(latest_review, performance_metrics)
    context = {
        "request": request,
        "app_title": "Vigilant Curator",
        "page_title": PAGE_TITLES.get(selected_tab, PAGE_TITLES[DEFAULT_TAB]),
        "dashboard": dashboard_payload,
        "selected_tab": selected_tab,
        "upload_feedback": upload_feedback,
        "detection_feedback": detection_feedback,
        "candidate_feedback": candidate_feedback,
        "recent_uploads": recent_uploads,
        "recent_results": recent_results,
        "latest_review": latest_review,
        "review_candidate": review_candidate,
        "review_risk_message": review_risk_message,
        "students_total": students_total,
        "students_high_risk": students_high_risk,
        "room_cards": room_cards,
        "anomaly_type_metrics": anomaly_type_metrics,
        "processing_power_bars": processing_power_bars,
        "violation_trend": violation_trend,
        "system_status": system_status,
        "performance_metrics": performance_metrics,
        "ai_settings": ai_settings,
        "face_candidates": face_candidates,
    }
    return context


def _build_students_csv(students: list[dict]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "STT",
            "Thi sinh",
            "Email",
            "Ma thi sinh",
            "Phong thi",
            "So canh bao",
            "Muc do rui ro",
            "So lan hau kiem",
            "Hanh vi ghi nhan",
            "Ket luan",
        ]
    )
    for index, student in enumerate(students, start=1):
        writer.writerow(
            [
                index,
                str(student.get("name") or ""),
                str(student.get("email") or ""),
                str(student.get("candidate_id") or ""),
                str(student.get("room") or ""),
                int(student.get("alerts") or 0),
                _risk_badge_vi(str(student.get("risk") or "low")),
                int(student.get("review_count") or 0),
                " | ".join([str(item).strip() for item in (student.get("behaviors") or []) if str(item).strip()]),
                _teacher_verdict_label_for_export(student),
            ]
        )
    return f"\ufeffsep=;\r\n{buffer.getvalue()}"


def _build_students_excel_html(students: list[dict]) -> str:
    generated_at = datetime.now(VIETNAM_TZ).strftime("%d/%m/%Y %H:%M:%S")
    rows_html: list[str] = []
    for index, student in enumerate(students, start=1):
        behaviors = "<br>".join(
            [
                escape(str(item).strip())
                for item in (student.get("behaviors") or [])
                if str(item).strip()
            ]
        )
        rows_html.append(
            "".join(
                [
                    "<tr>",
                    f"<td>{index}</td>",
                    f"<td>{escape(str(student.get('name') or ''))}</td>",
                    f"<td>{escape(str(student.get('email') or ''))}</td>",
                    f"<td>{escape(str(student.get('candidate_id') or ''))}</td>",
                    f"<td>{escape(str(student.get('room') or ''))}</td>",
                    f"<td>{int(student.get('alerts') or 0)}</td>",
                    f"<td>{escape(_risk_badge_vi(str(student.get('risk') or 'low')))}</td>",
                    f"<td>{int(student.get('review_count') or 0)}</td>",
                    f"<td>{behaviors}</td>",
                    f"<td>{escape(_teacher_verdict_label_for_export(student))}</td>",
                    "</tr>",
                ]
            )
        )

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{
      font-family: Arial, sans-serif;
      font-size: 12pt;
      color: #0f172a;
    }}
    .report-title {{
      font-size: 18pt;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .report-meta {{
      color: #475569;
      margin-bottom: 16px;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
    }}
    th, td {{
      border: 1px solid #cbd5e1;
      padding: 8px 10px;
      vertical-align: top;
    }}
    th {{
      background: #dbeafe;
      font-weight: 700;
      text-align: center;
    }}
    td {{
      background: #ffffff;
    }}
    .number {{
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="report-title">Bao cao thi sinh va vi pham hau kiem</div>
  <div class="report-meta">Ngay xuat: {escape(generated_at)}</div>
  <table>
    <thead>
      <tr>
        <th>STT</th>
        <th>Thi sinh</th>
        <th>Email</th>
        <th>Ma thi sinh</th>
        <th>Phong thi</th>
        <th>So canh bao</th>
        <th>Muc do rui ro</th>
        <th>So lan hau kiem</th>
        <th>Hanh vi ghi nhan</th>
        <th>Ket luan</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse, tags=["web"])
async def dashboard_page(request: Request) -> HTMLResponse:
    context = build_dashboard_context(request)
    return templates.TemplateResponse("dashboard.html", context)


@router.get("/students/export.csv", name="export_students_csv", tags=["web"])
async def export_students_csv(request: Request) -> Response:
    context = build_dashboard_context(request)
    students = context.get("dashboard", {}).get("students", [])
    csv_content = _build_students_csv(students if isinstance(students, list) else [])
    filename = f"students_report_{datetime.now().strftime('%Y-%m-%d')}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/students/export.xls", name="export_students_excel", tags=["web"])
async def export_students_excel(request: Request) -> Response:
    context = build_dashboard_context(request)
    students = context.get("dashboard", {}).get("students", [])
    excel_html = _build_students_excel_html(students if isinstance(students, list) else [])
    filename = f"students_report_{datetime.now().strftime('%Y-%m-%d')}.xls"
    return Response(
        content=excel_html,
        media_type="application/vnd.ms-excel; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/review/upload", name="upload_review_video", tags=["web"])
async def upload_review_video(video_files: list[UploadFile] = File(...)) -> RedirectResponse:
    try:
        if not video_files:
            raise ValueError("Vui long chon it nhat 1 video de tai len.")

        successful_uploads: list[dict] = []
        upload_failures: list[str] = []
        detection_successes: list[str] = []
        detection_warnings: list[str] = []
        detection_failures: list[str] = []

        detection_service.apply_runtime_settings(get_ai_settings())

        for video_file in video_files:
            try:
                upload_info = await video_service.save_upload(video_file)
            except (ValueError, OSError) as upload_exc:
                file_name = str(getattr(video_file, "filename", "") or "video_khong_xac_dinh")
                upload_failures.append(f"{file_name}: {upload_exc}")
                continue

            successful_uploads.append(upload_info)
            sql_storage_service.save_upload(upload_info)

            try:
                detection_info = detection_service.detect_from_video(upload_info["path"])
                sql_storage_service.save_review_result(detection_info, upload_info=upload_info)
                if detection_info["status"] == "completed":
                    detection_successes.append(str(upload_info.get("original_filename") or "video"))
                else:
                    detection_warnings.append(
                        f"{upload_info['original_filename']}: {detection_info['message']}"
                    )
            except (RuntimeError, FileNotFoundError, ValueError) as detection_exc:
                detection_failures.append(f"{upload_info['original_filename']}: {detection_exc}")

        upload_status = _build_batch_status(len(successful_uploads), len(upload_failures))
        upload_message = _build_upload_batch_message(successful_uploads, upload_failures)
        query_params = {
            "tab": "review",
            "upload_status": upload_status,
            "upload_message": upload_message,
        }

        if successful_uploads:
            detection_status = _build_batch_status(
                len(detection_successes),
                len(detection_failures),
                warning_count=len(detection_warnings),
            )
            detection_message = _build_detection_batch_message(
                detection_successes=detection_successes,
                detection_warnings=detection_warnings,
                detection_failures=detection_failures,
            )
            query_params["detection_status"] = detection_status
            query_params["detection_message"] = detection_message

        query = urlencode(query_params)
    except ValueError as exc:
        query = urlencode(
            {
                "tab": "review",
                "upload_status": "error",
                "upload_message": str(exc),
            }
        )

    return RedirectResponse(url=f"/?{query}", status_code=303)


@router.post("/review/live/start", tags=["web"])
async def start_live_review() -> JSONResponse:
    detection_service.apply_runtime_settings(get_ai_settings())
    payload = detection_service.reset_live_session()
    return JSONResponse(
        {
            "status": "success",
            "message": payload.get("message") or "Da khoi tao phien live test.",
        }
    )


@router.post("/review/live/frame", tags=["web"])
async def analyze_live_review_frame(frame: UploadFile = File(...)) -> JSONResponse:
    if cv2 is None or np is None:
        return JSONResponse(
            {"status": "error", "message": "Thieu opencv hoac numpy de xu ly webcam."},
            status_code=500,
        )

    image_bytes = await frame.read()
    await frame.close()
    if not image_bytes:
        return JSONResponse({"status": "error", "message": "Khong nhan duoc frame webcam."}, status_code=400)

    frame_array = np.frombuffer(image_bytes, dtype=np.uint8)
    frame_bgr = cv2.imdecode(frame_array, cv2.IMREAD_COLOR)
    if frame_bgr is None:
        return JSONResponse({"status": "error", "message": "Khong giai ma duoc frame webcam."}, status_code=400)

    try:
        detection_service.apply_runtime_settings(get_ai_settings())
        payload = detection_service.analyze_live_frame(frame_bgr)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    return JSONResponse(payload)


@router.post("/settings", tags=["web"])
async def save_settings(payload: SettingsPayload) -> JSONResponse:
    saved_settings = settings_service.save(payload.model_dump())
    detection_service.apply_runtime_settings(saved_settings)
    return JSONResponse(
        {
            "status": "success",
            "message": "Da luu cau hinh AI. Lan hau kiem tiep theo se dung gia tri moi.",
            "settings": saved_settings,
        }
    )


@router.post("/settings/reset", tags=["web"])
async def reset_settings() -> JSONResponse:
    saved_settings = settings_service.reset()
    detection_service.apply_runtime_settings(saved_settings)
    return JSONResponse(
        {
            "status": "success",
            "message": "Da khoi phuc cau hinh mac dinh.",
            "settings": saved_settings,
            "defaults": DEFAULT_AI_SETTINGS,
        }
    )


@router.post("/settings/candidates", tags=["web"])
async def save_face_candidate(
    candidate_id: str = Form(...),
    candidate_name: str = Form(...),
    candidate_email: str = Form(""),
    candidate_room: str = Form(""),
    candidate_image: UploadFile | None = File(None),
) -> RedirectResponse:
    try:
        profile = await candidate_registry_service.save_candidate(
            candidate_id=candidate_id,
            name=candidate_name,
            email=candidate_email,
            room=candidate_room,
            image_file=candidate_image,
        )
        sql_storage_service.update_candidate_profile(profile)
        face_status = detection_service.reload_face_recognition_gallery()
        stored_candidate_id = str(profile.get("candidate_id") or "")
        candidate_name_label = str(profile.get("name") or stored_candidate_id)
        if detection_service.has_face_candidate(stored_candidate_id):
            action = "cap nhat" if profile.get("updated") else "them"
            status = "success"
            message = f"Da {action} thi sinh {candidate_name_label} vao face gallery."
        else:
            status = "warning"
            message = (
                f"Da luu ho so {candidate_name_label}, nhung InsightFace chua doc duoc khuon mat trong anh. "
                "Hay thu anh ro mat hon."
            )
        if face_status.get("message") and status != "success":
            message = f"{message} ({face_status['message']})"
    except ValueError as exc:
        status = "error"
        message = str(exc)
    except OSError as exc:
        status = "error"
        message = f"Khong the luu anh thi sinh: {exc}"

    query = urlencode(
        {
            "tab": "settings",
            "candidate_status": status,
            "candidate_message": message,
        }
    )
    return RedirectResponse(url=f"/?{query}", status_code=303)


@router.post("/settings/candidates/delete", tags=["web"])
async def delete_face_candidate(candidate_id: str = Form(...)) -> RedirectResponse:
    try:
        profile = candidate_registry_service.delete_candidate(candidate_id)
        detection_service.reload_face_recognition_gallery()
        status = "success"
        message = f"Da xoa thi sinh {profile['name']} khoi face gallery."
    except ValueError as exc:
        status = "error"
        message = str(exc)
    except OSError as exc:
        status = "error"
        message = f"Khong the xoa thi sinh: {exc}"

    query = urlencode(
        {
            "tab": "settings",
            "candidate_status": status,
            "candidate_message": message,
        }
    )
    return RedirectResponse(url=f"/?{query}", status_code=303)


@router.post("/review/decision", tags=["web"])
async def update_review_decision(payload: ReviewDecisionPayload) -> JSONResponse:
    decision = str(payload.decision or "").strip().lower()
    if decision not in {"confirmed", "dismissed"}:
        return JSONResponse({"status": "error", "message": "Quyet dinh khong hop le."}, status_code=400)

    teacher_review = detection_service.update_result_decision(
        decision=decision,
        result_path=str(payload.result_path or ""),
        video_path=str(payload.video_path or ""),
    )
    if teacher_review is None:
        return JSONResponse({"status": "error", "message": "Khong tim thay ket qua de cap nhat."}, status_code=404)

    if sql_storage_service.is_available():
        sql_storage_service.update_review_decision(
            decision=decision,
            result_path=str(payload.result_path or ""),
            video_path=str(payload.video_path or ""),
        )

    success_message = (
        "Da xac nhan gian lan cho lan hau kiem nay."
        if decision == "confirmed"
        else "Da danh dau bo qua cho lan hau kiem nay."
    )
    return JSONResponse(
        {
            "status": "success",
            "message": success_message,
            "teacher_review": teacher_review,
        }
    )


@router.get("/review/candidate/{candidate_id}", tags=["web"])
async def get_candidate_review(candidate_id: str) -> JSONResponse:
    normalized_candidate_id = str(candidate_id or "").strip()
    if not normalized_candidate_id:
        return JSONResponse({"status": "error", "message": "Ma thi sinh khong hop le."}, status_code=400)

    review_payload = sql_storage_service.get_latest_review_result_for_candidate(normalized_candidate_id)
    if review_payload is None:
        review_payload = detection_service.get_latest_result_payload_for_candidate(normalized_candidate_id)
    if review_payload is None:
        return JSONResponse(
            {"status": "error", "message": "Khong tim thay lan hau kiem gan nhat cua thi sinh nay."},
            status_code=404,
        )

    return JSONResponse(
        {
            "status": "success",
            "review": _hydrate_review_payload(review_payload),
        }
    )
