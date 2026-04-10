from fastapi import APIRouter

from app.services.dashboard_service import get_dashboard_payload
from app.services.sql_storage_service import sql_storage_service


router = APIRouter(tags=["api"])


@router.get("/dashboard")
async def dashboard_data() -> dict:
    return get_dashboard_payload()


@router.get("/storage/status")
async def storage_status() -> dict:
    return sql_storage_service.get_status()


@router.get("/storage/recent-reviews")
async def storage_recent_reviews(limit: int = 10) -> dict:
    return {"items": sql_storage_service.list_recent_reviews(limit=limit)}
