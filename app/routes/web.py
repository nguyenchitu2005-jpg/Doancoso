import csv
from datetime import datetime, timedelta, timezone
from html import escape
from io import StringIO
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Request, UploadFile
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
from app.services.detection_service import DetectionService
from app.services.settings_service import DEFAULT_AI_SETTINGS, get_ai_settings, settings_service
from app.services.sql_storage_service import sql_storage_service
from app.services.video_service import VideoService


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
video_service = VideoService()
detection_service = DetectionService()
VALID_TABS = {"overview", "review", "students", "settings"}
VIETNAM_TZ = timezone(timedelta(hours=7))


class SettingsPayload(BaseModel):
    confidence_threshold: float
    extraction_interval_seconds: float
    behavior_threshold: float
    enable_gaze_alerts: bool
    enable_cell_phone_alerts: bool
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
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return ""
    try:
        normalized = raw_text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(VIETNAM_TZ).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return raw_text


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
    else:
        latency_label = "--"

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
        try:
            normalized = str(created_at).replace("Z", "+00:00")
            updated_at = datetime.fromisoformat(normalized)
            updated_label = f"Cập nhật: {updated_at.strftime('%d/%m/%Y %H:%M:%S')}"
        except ValueError:
            updated_label = f"Cập nhật: {created_at}"

    return {
        "latency_label": performance_metrics.get("latency_label", "--"),
        "frame_fps_label": frame_fps_label,
        "face_engine_label": face_engine_label,
        "state_label": state_label,
        "updated_label": updated_label,
    }


def build_latest_review_payload(recent_uploads: list[dict]) -> dict:
    latest_review = {
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
    }

    latest_result = sql_storage_service.get_latest_review_result() or detection_service.get_latest_result()
    if latest_result is not None:
        latest_review["status"] = latest_result.get("status", "unknown")
        latest_review["analysis_mode"] = latest_result.get("analysis_mode", "n/a")
        latest_review["summary"] = latest_result.get("summary", latest_review["summary"])
        latest_review["incidents"] = latest_result.get("incidents", [])
        latest_review["students_report"] = latest_result.get(
            "students_report",
            latest_review["summary"].get("students_report", []),
        )
        latest_review["engines"] = latest_result.get("engines", {})
        latest_review["teacher_review"] = latest_result.get("teacher_review", latest_review["teacher_review"])
        latest_review["primary_candidate"] = latest_result.get(
            "primary_candidate",
            latest_review["summary"].get("primary_candidate"),
        )
        if latest_review["primary_candidate"] is None:
            latest_review["primary_candidate"] = _pick_primary_candidate_from_students(latest_review["students_report"])
        latest_review["message"] = latest_result.get("message", latest_review["message"])
        latest_review["created_at"] = latest_result.get("created_at")
        latest_review["result_path"] = latest_result.get("result_path")
        latest_review["video_path"] = latest_result.get("video_path")

        video_path = latest_result.get("video_path")
        if video_path:
            video_file = Path(video_path)
            latest_review["video_name"] = video_file.name
            if video_file.exists():
                latest_review["video_url"] = f"/uploads/{video_file.name}"
        return latest_review

    if recent_uploads:
        latest_file = recent_uploads[0]["filename"]
        latest_review["video_name"] = latest_file
        latest_review["video_url"] = f"/uploads/{latest_file}"
        latest_review["message"] = "Video da tai len, chua co ket qua phan tich."

    return latest_review


def build_dashboard_context(request: Request) -> dict:
    selected_tab = request.query_params.get("tab", "overview")
    if selected_tab not in VALID_TABS:
        selected_tab = "overview"

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

    recent_uploads = video_service.list_uploads()
    latest_review = build_latest_review_payload(recent_uploads)
    ai_settings = get_ai_settings()
    db_recent_results = sql_storage_service.list_recent_reviews(limit=5)
    historical_students = sql_storage_service.list_candidate_histories(limit=250)
    if not historical_students:
        historical_students = detection_service.list_historical_students()

    dashboard_payload = get_dashboard_payload()
    latest_students_report = latest_review.get("students_report", [])
    if latest_students_report:
        high_risk = len([item for item in latest_students_report if item.get("risk") == "high"])
        dashboard_payload["overview"]["active_sessions"] = len(latest_students_report)
        dashboard_payload["overview"]["integrity_score"] = f"{max(0.0, 100.0 - (high_risk * 8.0)):.1f}%"
    source_students = historical_students or latest_students_report
    dashboard_payload["students"] = [dict(student) for student in source_students]
    student_items = dashboard_payload.get("students", [])
    students_total = len(student_items)
    students_high_risk = len([item for item in student_items if str(item.get("risk") or "") == "high"])

    review_candidate = latest_review.get("primary_candidate") or _pick_primary_candidate_from_students(latest_students_report) or {
        "candidate_id": "UNKNOWN",
        "name": "Unknown Candidate",
        "email": "",
        "room": "",
        "alerts": 0,
        "risk": "low",
        "behaviors": [],
    }
    review_candidate["risk_label"] = _risk_label_vi(str(review_candidate.get("risk") or "low"))
    review_candidate["device_status"] = _review_device_status(latest_review)
    review_candidate["behaviors"] = list(review_candidate.get("behaviors") or [])
    review_candidate["avatar"] = "".join(
        [part[0] for part in str(review_candidate.get("name") or "UC").split() if part][:2]
    ).upper() or "UC"

    review_incident_count = len(latest_review.get("incidents", []))
    review_risk_message = (
        f"He thong da ghi nhan {review_incident_count} su co trong lan hau kiem gan nhat."
        if review_incident_count > 0
        else "Chua ghi nhan su co trong lan hau kiem gan nhat."
    )
    performance_metrics = build_performance_metrics(latest_review)
    recent_incident_timestamps = sql_storage_service.list_recent_incident_timestamps(hours=24)
    violation_trend = build_violation_trend(
        latest_review,
        trend_timestamps=recent_incident_timestamps,
    )
    system_status = build_system_status(latest_review, performance_metrics)

    context = {
        "request": request,
        "app_title": "Vigilant Curator",
        "page_title": "System Oversight",
        "dashboard": dashboard_payload,
        "selected_tab": selected_tab,
        "upload_feedback": upload_feedback,
        "detection_feedback": detection_feedback,
        "recent_uploads": recent_uploads,
        "recent_results": db_recent_results or detection_service.list_results(),
        "latest_review": latest_review,
        "review_candidate": review_candidate,
        "review_risk_message": review_risk_message,
        "students_total": students_total,
        "students_high_risk": students_high_risk,
        "violation_trend": violation_trend,
        "system_status": system_status,
        "performance_metrics": performance_metrics,
        "ai_settings": ai_settings,
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
async def upload_review_video(video_file: UploadFile = File(...)) -> RedirectResponse:
    try:
        upload_info = await video_service.save_upload(video_file)
        sql_storage_service.save_upload(upload_info)
        upload_status = "success"
        upload_message = f"Tai len thanh cong: {upload_info['original_filename']} ({upload_info['size_label']})."

        try:
            detection_service.apply_runtime_settings(get_ai_settings())
            detection_info = detection_service.detect_from_video(upload_info["path"])
            sql_storage_service.save_review_result(detection_info, upload_info=upload_info)
            detection_status = "success" if detection_info["status"] == "completed" else "warning"
            detection_message = detection_info["message"]
        except (RuntimeError, FileNotFoundError, ValueError) as detection_exc:
            detection_status = "error"
            detection_message = str(detection_exc)

        query = urlencode(
            {
                "tab": "review",
                "upload_status": upload_status,
                "upload_message": upload_message,
                "detection_status": detection_status,
                "detection_message": detection_message,
            }
        )
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
