from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services.dashboard_service import get_dashboard_payload
from app.services.detection_service import DetectionService
from app.services.video_service import VideoService


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
video_service = VideoService()
detection_service = DetectionService()
VALID_TABS = {"overview", "review", "students", "settings"}


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

    context = {
        "request": request,
        "app_title": "Vigilant Curator",
        "page_title": "System Oversight",
        "dashboard": get_dashboard_payload(),
        "selected_tab": selected_tab,
        "upload_feedback": upload_feedback,
        "detection_feedback": detection_feedback,
        "recent_uploads": video_service.list_uploads(),
        "recent_results": detection_service.list_results(),
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
