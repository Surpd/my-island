"""Run a disposable PostgreSQL stage-4 constructor publication check.

This intentionally accepts only the named loopback database used by the
temporary PostgreSQL release harness. It never reads project environment files.
Set MY_ISLAND_RELEASE_TEST_DATABASE_URL explicitly when invoking this script.
"""
from __future__ import annotations

import os
# Set before importing application modules: config.py must not load real .env.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services import canonical_schedule as canonical
from backend.services.canonical_weekly_refresh import refresh_current_week
from backend.services.constructor_publication import (
    PublicationConflict,
    list_publications,
    preview_publication,
    preview_restore,
    publish_publication,
    restore_publication,
)
from backend.services.schedule_experiments import create_experiment
from backend.services.schedule_experiments import (
    ExperimentConflict,
    create_experiment_week,
    get_experiment,
    save_experiment,
)
from backend.services.schedule_sheet_export import sheets_requests
from backend.services.schedule_sheet_publishing import prepare_export


ROOT = Path(__file__).resolve().parents[2]
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
ALLOWED_PORT = 55432
ALLOWED_DATABASE = "my_island_release_check"


def _database_url() -> str:
    value = os.environ.get("MY_ISLAND_RELEASE_TEST_DATABASE_URL", "")
    parsed = urlparse(value)
    if (parsed.scheme not in {"postgres", "postgresql"}
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.port != ALLOWED_PORT
            or parsed.path.lstrip("/") != ALLOWED_DATABASE):
        raise SystemExit(
            "Refusing to run: set MY_ISLAND_RELEASE_TEST_DATABASE_URL to the "
            "temporary loopback database on 127.0.0.1:55432/my_island_release_check"
        )
    return value


def _apply_migrations(url: str) -> None:
    # The three roles are local-only prerequisites for migration 041 grants.
    with psycopg.connect(url) as connection:
        for role in ("anon", "authenticated", "service_role"):
            cursor = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            )
            if cursor.fetchone() is None:
                connection.execute(f'CREATE ROLE "{role}" NOLOGIN')
        for name in ("041_teacher_ui_colors.sql", "042_constructor_publications.sql"):
            connection.execute((ROOT / "backend" / "migrations" / name).read_text(encoding="utf-8"))
        connection.commit()


def run() -> dict[str, object]:
    url = _database_url()
    _apply_migrations(url)
    _apply_migrations(url)  # Verify both release migrations are repeatable.
    database = Database(database_url=url)

    with database.connection() as connection:
        existing_head = database.execute(
            connection, "SELECT 1 FROM schedule_publication_heads LIMIT 1"
        ).fetchone()
        existing_version = database.execute(
            connection, "SELECT 1 FROM canonical_schedule_versions LIMIT 1"
        ).fetchone()
    if existing_head or existing_version:
        raise SystemExit(
            "Refusing to run against a non-empty publication database; restore the "
            "schema-only dump to this disposable database first"
        )

    # Verify the harness is exercising genuine PostgreSQL native values.
    with psycopg.connect(url) as connection:
        info = connection.execute(
            "SELECT current_setting('server_version_num')::int, 'uuid'::regtype::oid, "
            "'time without time zone'::regtype::oid, 'jsonb'::regtype::oid"
        ).fetchone()
        if not info or info[0] < 170000 or not all(info[1:]):
            raise RuntimeError("Expected PostgreSQL 17 with native UUID, TIME, and JSONB types")

    suffix = uuid4().hex[:12]
    class_name = f"9A Release {suffix}"
    group = database.create_group(class_name, "class", base_class_name=class_name)
    teacher = database.create_identity("teacher", f"Release teacher {suffix}")
    student = database.create_identity("student", f"Release student {suffix}", class_name)
    database.create_membership(group["id"], student["id"], "student", "admin_override", suffix)

    lesson = {
        "id": f"lesson-{suffix}", "day": 0, "period": 0, "classId": str(group["id"]),
        "teacherId": str(teacher["id"]), "subject": "Math", "room": "R1",
        "audience": {"kind": "classes", "class_ids": [str(group["id"])]},
    }

    def draft(subject: str = "Math"):
        current = dict(lesson, subject=subject)
        return create_experiment(database, name=f"Release {suffix}", source="import", payload={
            "reference_date": "2026-10-19", "days": [0], "classes": [str(group["id"])],
            "slots": [{"start": "09:00", "end": "09:45"}], "lessons": [current],
        })

    def publish(item, scope: str, week_start: str | None = None):
        args = {"scope_kind": scope, "expected_revision": item["revision"]}
        if week_start:
            args["week_start"] = week_start
        preview = preview_publication(database, item["id"], **args)
        if not preview["can_publish"]:
            raise AssertionError(f"PostgreSQL preview blocked: {preview['blockers']}")
        result = publish_publication(
            database, item["id"], **args, preview_token=preview["preview_token"], confirmed=True,
        )
        item["revision"] = result["draft_revision"]
        if not result["runtime_readback"]["verified"]:
            raise AssertionError(f"PostgreSQL runtime readback failed: {result['runtime_readback']}")
        return result

    # Draft edits round-trip through PostgreSQL JSONB, and stale revisions are
    # exposed as the established HTTP 409 response with the current revision.
    editable = draft()
    edited_payload = deepcopy(editable["payload"])
    edited_payload["lessons"][0]["room"] = "R2"
    saved = save_experiment(database, editable["id"], expected_revision=1, payload=edited_payload)
    persisted_edit = get_experiment(database, editable["id"])
    if saved["revision"] != 2 or persisted_edit["payload"]["lessons"][0]["room"] != "R2":
        raise AssertionError(f"PostgreSQL draft edit readback failed: {persisted_edit}")
    try:
        save_experiment(database, editable["id"], expected_revision=1, payload=editable["payload"])
    except ExperimentConflict as error:
        if error.current.get("revision") != 2:
            raise AssertionError(f"Stale save conflict did not carry revision 2: {error.current}") from error
    else:
        raise AssertionError("PostgreSQL stale draft save was not rejected")
    admin = database.get_or_create_user(990000001, "admin")
    challenge, _ = database.create_admin_login_challenge(admin["id"])
    token, _ = database.consume_admin_login_challenge(challenge)
    app = FastAPI()
    app.include_router(create_router(database))
    response = TestClient(app, raise_server_exceptions=False).put(
        f"/api/admin/schedule/experiments/{editable['id']}",
        headers={"X-Dev-Auth": f"browser:{token}"},
        json={"expected_revision": 1, "payload": editable["payload"]},
    )
    route_current_revision = None
    if response.status_code == 409:
        route_current_revision = response.json().get("detail", {}).get("current", {}).get("revision")
    if response.status_code != 409 or route_current_revision != 2:
        raise AssertionError(
            f"Stale save route must return HTTP 409/current revision 2; "
            f"got HTTP {response.status_code}: {response.text[:240]}"
        )

    # Migration 041 is also exercised with a native UUID FK.
    with database.connection() as connection:
        database.execute(connection, """INSERT INTO teacher_ui_colors
            (school_id, teacher_identity_id, base_color, manual_color, base_source,
             source_evidence, revision, updated_at)
            VALUES (?, ?, '#123456', '#654321', 'fallback', NULL, 1, now())""",
            (f"release-{suffix}", teacher["id"]))
        color = database.execute(connection,
            "SELECT manual_color FROM teacher_ui_colors WHERE school_id = ? AND teacher_identity_id = ?",
            (f"release-{suffix}", teacher["id"])).fetchone()
    if not color or color["manual_color"] != "#654321":
        raise AssertionError(f"PostgreSQL teacher color migration roundtrip failed: {color}")

    initial = draft()
    template = publish(initial, "template")
    copied_week = create_experiment_week(
        database, initial["id"], week_start="2026-10-26", expected_revision=initial["revision"]
    )
    if (copied_week["base_version_id"] != template["published_id"]
            or copied_week["payload"].get("source_template", {}).get("id") != initial["id"]
            or copied_week["payload"].get("week_start") != "2026-10-26"):
        raise AssertionError(f"PostgreSQL week copy provenance/readback failed: {copied_week}")
    rendered_export = prepare_export(database, copied_week["id"], expected_revision=copied_week["revision"])
    rendered_requests = sheets_requests(rendered_export["plan"])
    if (rendered_export["status"] != "prepared" or not rendered_export["plan"].get("cells")
            or not rendered_requests or rendered_export.get("url")):
        raise AssertionError("PostgreSQL render-only export did not prepare a plan without publishing to Google")
    weekly = publish(initial, "week", "2026-10-19")
    source_refresh = refresh_current_week(database, None, "2026-10-19")
    if source_refresh["status"] != "manual_publication_protected":
        raise AssertionError(f"PostgreSQL manual source hold failed: {source_refresh}")
    projection = canonical.project_student(database, student["id"], "2026-10-19")
    if not projection["items"] or projection["items"][0]["activity"] != "Math":
        raise AssertionError(f"PostgreSQL membership projection failed: {projection}")

    # Deliberately fail the template runtime readback after artifacts/head have
    # been written inside the transaction; none may survive the rollback.
    rollback_draft = draft()
    rollback_preview = preview_publication(
        database, rollback_draft["id"], scope_kind="template", expected_revision=rollback_draft["revision"]
    )
    with database.connection() as connection:
        before_rollback = {
            table: database.execute(connection, f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            for table in ("canonical_schedule_versions", "canonical_schedule_blocks",
                          "canonical_schedule_assignments", "schedule_publication_heads",
                          "schedule_constructor_publications")
        }
    original_readback = canonical.read_canonical_template

    def hide_runtime_template(db, version_id=None):
        return None if version_id is None else original_readback(db, version_id)

    try:
        with patch.object(canonical, "read_canonical_template", side_effect=hide_runtime_template):
            publish_publication(
                database, rollback_draft["id"], scope_kind="template",
                expected_revision=rollback_draft["revision"],
                preview_token=rollback_preview["preview_token"], confirmed=True,
            )
    except RuntimeError as error:
        if "rolled back" not in str(error):
            raise
    else:
        raise AssertionError("Injected runtime verification failure did not abort publication")
    with database.connection() as connection:
        after_rollback = {
            table: database.execute(connection, f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            for table in before_rollback
        }
    if before_rollback != after_rollback:
        raise AssertionError(f"Publication rollback left partial canonical/head/audit rows: {before_rollback} -> {after_rollback}")

    changed = draft("English")
    publish(changed, "template")
    try:
        preview_restore(database, template["publication_id"], expected_head_revision=1)
    except PublicationConflict:
        pass
    else:
        raise AssertionError("PostgreSQL restore CAS did not reject a stale head")
    current = preview_restore(database, template["publication_id"], expected_head_revision=2)
    restored = restore_publication(
        database, template["publication_id"], expected_head_revision=2,
        preview_token=current["preview_token"], confirmed=True,
    )
    if restored["published_id"] == template["published_id"]:
        raise AssertionError("Restore must create a new immutable canonical artifact")
    current_template = canonical.read_canonical_template(database)
    assignment = next(iter(current_template["blocks"].values()))["assignments"][0]
    if assignment["activity"] != "Math":
        raise AssertionError(f"PostgreSQL template restore content mismatch: {assignment}")

    return {
        "postgres_version_num": info[0], "migration_041": "applied",
        "migration_042": "applied", "migration_idempotence": "041+042 rerun passed",
        "template_published": template["published_id"],
        "week_published": weekly["published_id"], "restore": restored["published_id"],
        "source_hold": source_refresh["status"], "teacher_color": color["manual_color"],
        "draft_edit_revision": persisted_edit["revision"], "stale_save_http": response.status_code,
        "stale_save_current_revision": route_current_revision,
        "week_copy_base": copied_week["base_version_id"], "render_only_export": rendered_export["status"],
        "rollback": "no_partial_rows",
        "publication_count": len(list_publications(database)["items"]),
        "synthetic_scope": f"release-{suffix}",
    }


if __name__ == "__main__":
    print(run())
