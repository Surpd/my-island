"""School-only explicit constructor publication, disabled unless opted in."""
from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.services.constructor_publication import (
    PublicationBlocked, PublicationConflict, preview_publication,
    publish_publication, list_publications, restore_publication, preview_restore,
)
from backend.services.schedule_experiments import ExperimentNotFound


class PreviewBody(BaseModel):
    scope_kind: str
    expected_revision: int = Field(ge=1)
    week_start: str | None = None


class PublishBody(PreviewBody):
    preview_token: str
    confirmed: bool = False


class RestorePreviewBody(BaseModel):
    publication_id: str
    expected_head_revision: int = Field(ge=1)


class RestoreBody(RestorePreviewBody):
    preview_token: str
    confirmed: bool = False


def create_constructor_publication_router(
    resolve_stores: Callable[[], tuple[Any, Any]],
    require_school_admin: Callable[[str | None], Any],
) -> APIRouter:
    """Caller supplies existing trusted school-session and store adapters.

    resolve_stores returns (directory/source, drafts). A read-only source plus
    separate draft DB is intentionally insufficient for atomic publication.
    """
    router = APIRouter(prefix="/api/admin/schedule/constructor")

    def admin(token: str | None, *, preview_only: bool = False) -> tuple[Any, Any]:
        if os.getenv("MY_ISLAND_CONSOLIDATED_SCHEDULE") != "1":
            raise HTTPException(404, "Constructor publication is disabled")
        actor = require_school_admin(token)
        source, drafts = resolve_stores()
        same = source is drafts or (not source.database_url and not drafts.database_url
                                    and str(source.path) == str(drafts.path))
        if preview_only:
            return source, None if same else drafts
        if not same:
            raise HTTPException(409, "Publication requires one approved transactional school database; split source/draft stores are read-only")
        if isinstance(actor, tuple):
            actor = actor[1] if len(actor) > 1 else actor[0]
        elif isinstance(actor, dict):
            actor = actor.get("id") or actor.get("user_id")
        return drafts, actor

    def call(fn: Callable, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PublicationConflict as error:
            raise HTTPException(409, str(error)) from error
        except PublicationBlocked as error:
            raise HTTPException(422, error.preview) from error
        except ExperimentNotFound as error:
            raise HTTPException(404, "Constructor draft not found") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except Exception as error:
            if getattr(error, "sqlstate", None) in {"40001", "40P01"}:
                raise HTTPException(409, "Concurrent school data changed; review a fresh publication preview") from error
            raise

    @router.post("/{draft_id}/publication/preview")
    def preview(draft_id: str, body: PreviewBody, x_school_admin_session: str | None = Header(default=None)):
        database, draft_database = admin(x_school_admin_session, preview_only=True)
        return call(preview_publication, database, draft_id, draft_database=draft_database, **body.model_dump())

    @router.post("/{draft_id}/publication/publish")
    def publish(draft_id: str, body: PublishBody, x_school_admin_session: str | None = Header(default=None)):
        database, actor = admin(x_school_admin_session)
        return call(publish_publication, database, draft_id, actor_user_id=actor, **body.model_dump())

    @router.get("/{draft_id}/publications")
    def history(draft_id: str, x_school_admin_session: str | None = Header(default=None)):
        database, _ = admin(x_school_admin_session, preview_only=True)
        return call(list_publications, database, draft_id)

    @router.post("/publication/restore")
    def restore(body: RestoreBody, x_school_admin_session: str | None = Header(default=None)):
        database, actor = admin(x_school_admin_session)
        return call(restore_publication, database, actor_user_id=actor, **body.model_dump())

    @router.post("/publication/restore/preview")
    def restore_preview(body: RestorePreviewBody, x_school_admin_session: str | None = Header(default=None)):
        database, _ = admin(x_school_admin_session)
        return call(preview_restore, database, **body.model_dump())

    return router
