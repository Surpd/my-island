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
from backend.services.schedule_experiments import ExperimentNotFound, ExperimentConflict


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


class ResolutionBody(BaseModel):
    expected_revision: int = Field(ge=1)
    lesson_ids: list[str] = Field(min_length=1)


class ResolutionApplyBody(ResolutionBody):
    preview_token: str
    action: str
    reason: str = Field(min_length=1)
    room: str | None = None
    exception_id: str | None = None


class AudienceBulkPreviewBody(BaseModel):
    expected_revision: int = Field(ge=1)
    scope: str
    day: int | None = None
    class_id: str | None = None


class AudienceBulkApplyBody(AudienceBulkPreviewBody):
    preview_token: str
    reason: str = Field(min_length=1)


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
        except ExperimentConflict as error:
            raise HTTPException(409, 'Draft changed; reload before confirming') from error
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

    def resolution_stores(token):
        admin(token, preview_only=True)
        actor=require_school_admin(token)
        if isinstance(actor,tuple):actor=actor[1] if len(actor)>1 else actor[0]
        if isinstance(actor,dict):actor=actor.get('id') or actor.get('user_id')
        source,drafts=resolve_stores()
        if getattr(drafts,'read_only',False):raise HTTPException(409,'Draft store is read only')
        return source,drafts,actor

    @router.post('/{draft_id}/resolution/preview')
    def resolution_preview_route(draft_id: str,body:ResolutionBody,x_school_admin_session: str|None=Header(default=None)):
        from backend.services.constructor_resolution import resolution_preview
        source,drafts,_=resolution_stores(x_school_admin_session)
        return call(resolution_preview,drafts,draft_id,audience_database=source,**body.model_dump())

    @router.post('/{draft_id}/resolution/apply')
    def resolution_apply_route(draft_id: str,body:ResolutionApplyBody,x_school_admin_session: str|None=Header(default=None)):
        from backend.services.constructor_resolution import apply_resolution
        source,drafts,actor=resolution_stores(x_school_admin_session)
        return call(apply_resolution,drafts,draft_id,audience_database=source,actor_user_id=actor,**body.model_dump())

    @router.post('/{draft_id}/audiences/preview')
    def bulk_audience_preview_route(draft_id: str, body: AudienceBulkPreviewBody, x_school_admin_session: str | None = Header(default=None)):
        from backend.services.constructor_resolution import bulk_audience_preview
        source,drafts,_=resolution_stores(x_school_admin_session)
        return call(bulk_audience_preview,drafts,draft_id,audience_database=source,**body.model_dump())

    @router.post('/{draft_id}/audiences/apply')
    def bulk_audience_apply_route(draft_id: str, body: AudienceBulkApplyBody, x_school_admin_session: str | None = Header(default=None)):
        from backend.services.constructor_resolution import apply_bulk_audience
        source,drafts,actor=resolution_stores(x_school_admin_session)
        return call(apply_bulk_audience,drafts,draft_id,audience_database=source,actor_user_id=actor,**body.model_dump())

    @router.get('/publication/{publication_id}/source')
    def published_source_route(publication_id: str,x_school_admin_session: str|None=Header(default=None)):
        from backend.services.constructor_publication import get_published_source
        database,_=admin(x_school_admin_session,preview_only=True)
        return call(get_published_source,database,publication_id)

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
