from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates

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


class SettingsPayload(BaseModel):
    confidence_threshold: float
    extraction_interval_seconds: float
    behavior_threshold: float


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


def _review_device_status(latest_review: dict) -> str:
    engines = latest_review.get("engines", {}) if isinstance(latest_review, dict) else {}
    face_engine = engines.get("face_recognition", {}) if isinstance(engines, dict) else {}
    if face_engine.get("enabled"):
        return "Da xac minh"
    return "Cho xac minh"


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


def build_latest_review_payload(recent_uploads: list[dict]) -> dict:
    latest_review = {
        "status": "idle",
        "analysis_mode": "n/a",
        "video_name": None,
        "video_url": None,
        "summary": {"total_violations": 0, "reviewed_frames": 0},
        "incidents": [],
        "students_report": [],
        "primary_candidate": None,
        "engines": {},
        "message": "Chua co du lieu hau kiem.",
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
        latest_review["primary_candidate"] = latest_result.get(
            "primary_candidate",
            latest_review["summary"].get("primary_candidate"),
        )
        if latest_review["primary_candidate"] is None:
            latest_review["primary_candidate"] = _pick_primary_candidate_from_students(latest_review["students_report"])
        latest_review["message"] = latest_result.get("message", latest_review["message"])

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

    dashboard_payload = get_dashboard_payload()
    students_report = latest_review.get("students_report", [])
    if students_report:
        dashboard_payload["students"] = students_report
        high_risk = len([item for item in students_report if item.get("risk") == "high"])
        dashboard_payload["overview"]["active_sessions"] = len(students_report)
        dashboard_payload["overview"]["integrity_score"] = f"{max(0.0, 100.0 - (high_risk * 8.0)):.1f}%"

    review_candidate = latest_review.get("primary_candidate") or _pick_primary_candidate_from_students(students_report) or {
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
        "performance_metrics": build_performance_metrics(latest_review),
        "ai_settings": ai_settings,
    }
    return context


@router.get("/", response_class=HTMLResponse, tags=["web"])
async def dashboard_page(request: Request) -> HTMLResponse:
    context = build_dashboard_context(request)
    return templates.TemplateResponse("dashboard.html", context)


@router.post("/review/upload", name="upload_review_video", tags=["web"])
async def upload_review_video(video_file: UploadFile = File(...)) -> RedirectResponse:
    try:
        upload_info = await video_service.save_upload(video_file)
        sql_storage_service.save_upload(upload_info)
        upload_status = "success"
        upload_message = f"Tải lên thành công: {upload_info['original_filename']} ({upload_info['size_label']})."

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
