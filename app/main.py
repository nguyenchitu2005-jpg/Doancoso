from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes.api import router as api_router
from app.routes.web import router as web_router


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Vigilant Curator",
    description="AI proctoring dashboard scaffold built with FastAPI.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=BASE_DIR.parent / "uploads"), name="uploads")
app.mount("/results", StaticFiles(directory=BASE_DIR.parent / "results"), name="results")
app.include_router(web_router)
app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
