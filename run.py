import os

import uvicorn


if __name__ == "__main__":
    reload_enabled = os.getenv("UVICORN_RELOAD", "false").strip().lower() in {"1", "true", "yes", "on"}
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=reload_enabled)
