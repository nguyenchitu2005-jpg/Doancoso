from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.templating import Jinja2Templates

from app.services.dashboard_service import get_dashboard_payload
from app.services.detection_service import DetectionService
from app.services.settings_service import DEFAULT_AI_SETTINGS, get_ai_settings, settings_service
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


def build_latest_review_payload(recent_uploads: list[dict]) -> dict:
    latest_review = {
        "status": "idle",
        "analysis_mode": "n/a",
        "video_name": None,
        "video_url": None,
        "summary": {"total_violations": 0, "reviewed_frames": 0},
        "incidents": [],
        "message": "Chua co du lieu hau kiem.",
    }

    latest_result = detection_service.get_latest_result()
    if latest_result is not None:
        latest_review["status"] = latest_result.get("status", "unknown")
        latest_review["analysis_mode"] = latest_result.get("analysis_mode", "n/a")
        latest_review["summary"] = latest_result.get("summary", latest_review["summary"])
        latest_review["incidents"] = latest_result.get("incidents", [])
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

    context = {
        "request": request,
        "app_title": "Vigilant Curator",
        "page_title": "System Oversight",
        "dashboard": get_dashboard_payload(),
        "selected_tab": selected_tab,
        "upload_feedback": upload_feedback,
        "detection_feedback": detection_feedback,
        "recent_uploads": recent_uploads,
        "recent_results": detection_service.list_results(),
        "latest_review": latest_review,
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
        upload_status = "success"
        upload_message = f"Tải lên thành công: {upload_info['original_filename']} ({upload_info['size_label']})."

        try:
            detection_service.apply_runtime_settings(get_ai_settings())
            detection_info = detection_service.detect_from_video(upload_info["path"])
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
