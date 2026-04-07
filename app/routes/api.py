from fastapi import APIRouter

from app.services.dashboard_service import get_dashboard_payload


router = APIRouter(tags=["api"])


@router.get("/dashboard")
async def dashboard_data() -> dict:
    return get_dashboard_payload()
