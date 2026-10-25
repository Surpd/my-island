from __future__ import annotations

import os
import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.database import Database
from backend.config import get_settings
from backend.handlers.api import GENERIC_PREVIEW_SCHOOL_MODE_DETAIL, create_router
from backend.handlers.school_directory_api import create_school_directory_router
from backend.services.auth import is_loopback_dev_auth_request
from backend.services.consolidated_workspace import isolated_consolidation_enabled, require_consolidated_admin
from backend.handlers.constructor_publication_api import create_constructor_publication_router
from backend.handlers.teacher_ui_colors_api import create_school_teacher_ui_colors_router


settings = get_settings()
database = Database(settings.database_path, settings.database_url)
database.initialize()
if isolated_consolidation_enabled():
    from backend.services.constructor_publication import initialize_publication_schema
    from backend.services.teacher_ui_colors import initialize_sqlite_schema
    initialize_publication_schema(database)
    initialize_sqlite_schema(database)
app = FastAPI(title="My Island API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["*"])


@app.middleware("http")
async def forward_admin_browser_session(request, call_next):
    """Keep the session HttpOnly while making it available to existing handlers."""
    # APP_ENV/DEV_AUTH_ENABLED are operator configuration. The test/demo role
    # header must still not become a public auth bypass if either is wrong on a
    # reachable server. Only direct loopback requests may use those role tokens.
    dev_auth = request.headers.get("X-Dev-Auth")
    if dev_auth in {"student:1", "teacher:1", "admin:1"} and not is_loopback_dev_auth_request(
        request.headers.get("host"), request.client.host if request.client else None,
        local_test_mode=settings.local_test_mode,
    ):
        request.scope["headers"] = [
            (name, value) for name, value in request.scope.get("headers", [])
            if name.lower() != b"x-dev-auth"
        ]
    local_school_mode = (os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1" or
                         os.getenv("MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE") == "1")
    if (not settings.local_test_mode and local_school_mode and request.method == "POST"
            and request.url.path.startswith("/api/admin/previews")):
        return JSONResponse(status_code=409, content={"detail": GENERIC_PREVIEW_SCHOOL_MODE_DETAIL})
    if not settings.local_test_mode and os.getenv("MY_ISLAND_SCHOOL_WORKSPACE") == "1":
        if request.url.path == "/api/integrations/google/callback":
            return JSONResponse(status_code=403, content={"detail": "Google integration changes are disabled in local school mode"})
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            allowed_local_operations = {
                ("POST", "/api/admin/auth/exchange"),
                ("POST", "/api/admin/auth/logout"),
                ("POST", "/api/admin/school/auth/exchange"),
                ("POST", "/api/admin/school/auth/logout"),
                ("POST", "/api/admin/schedule/experiments/school/audience-preview"),
            }
            # These endpoints write only to school-experiments.sqlite3. The
            # source database still uses transaction-enforced read-only access.
            local_draft_operation = (
                request.method == "POST" and request.url.path == "/api/admin/schedule/experiments/school/import"
                or request.method in {"PUT", "DELETE"} and re.fullmatch(r"/api/admin/schedule/experiments/school/[^/]+", request.url.path)
                or request.method == "POST" and re.fullmatch(r"/api/admin/schedule/experiments/school/[^/]+/(duplicate|week|export)", request.url.path)
                or request.method in {"POST", "PUT"} and request.query_params.get("workspace") == "school"
                and re.fullmatch(r"/api/admin/schedule/activities(?:/[^/]+)?", request.url.path)
            )
            if (request.method, request.url.path) not in allowed_local_operations and not local_draft_operation:
                return JSONResponse(status_code=403, content={"detail": "Local school mode is read-only"})
    token = request.cookies.get("my_island_admin_session") or request.headers.get("X-Admin-Browser-Session")
    if token and not request.headers.get("X-Dev-Auth"):
        headers = list(request.scope.get("headers", []))
        headers.append((b"x-dev-auth", f"browser:{token}".encode("ascii")))
        request.scope["headers"] = headers
    return await call_next(request)

app.include_router(create_router(database))
app.include_router(create_school_directory_router(database))
app.include_router(create_constructor_publication_router(
    lambda: (database, database), lambda token: require_consolidated_admin(database, token)))
app.include_router(create_school_teacher_ui_colors_router(
    lambda: database, lambda token: require_consolidated_admin(database, token)))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env,
            "release": os.getenv("RENDER_GIT_COMMIT") or os.getenv("MY_ISLAND_RELEASE_SHA", "unversioned")}
