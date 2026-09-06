from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.database import Database
from backend.config import get_settings
from backend.handlers.api import create_router


settings = get_settings()
database = Database(settings.database_path, settings.database_url)
database.initialize()
app = FastAPI(title="My Island API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["*"])
app.include_router(create_router(database))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}
