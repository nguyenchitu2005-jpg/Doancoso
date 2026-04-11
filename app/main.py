import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.api import router as api_router
from app.routes.web import router as web_router
from app.services.sql_storage_service import sql_storage_service


BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR.parent / "uploads"
RESULTS_DIR = BASE_DIR.parent / "results"


def _reset_runtime_directory(target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for child in target_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            for nested in child.rglob("*"):
                if nested.is_file():
                    nested.unlink(missing_ok=True)
            for nested_dir in sorted([item for item in child.rglob("*") if item.is_dir()], reverse=True):
                nested_dir.rmdir()
            child.rmdir()


def _should_reset_runtime_on_startup() -> bool:
    return os.getenv("RESET_RUNTIME_ON_STARTUP", "false").strip().lower() in {"1", "true", "yes", "on"}

app = FastAPI(
    title="Vigilant Curator",
    description="AI proctoring dashboard scaffold built with FastAPI.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
app.mount("/results", StaticFiles(directory=RESULTS_DIR), name="results")
app.include_router(web_router)
app.include_router(api_router, prefix="/api")


@app.on_event("startup")
async def reset_runtime_data() -> None:
    if _should_reset_runtime_on_startup():
        _reset_runtime_directory(UPLOADS_DIR)
        _reset_runtime_directory(RESULTS_DIR)
    else:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if sql_storage_service.initialize():
        sql_storage_service.backfill_reviews_from_results_dir(RESULTS_DIR)
        sql_storage_service.backfill_video_hashes()
        sql_storage_service.rebuild_candidate_histories()


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
