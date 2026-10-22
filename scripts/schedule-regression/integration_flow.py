"""HTTP integration flow for the synthetic localhost consolidation backend."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / ".local-test" / "consolidation"


class FlowError(RuntimeError):
    pass


def database_snapshot(database_path: Path) -> dict[str, str]:
    uri = database_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        content = {}
        for name in tables:
            rows = connection.execute(f' SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            if name == "admin_browser_sessions":
                columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
                touch_index = columns.index("last_seen_at") if "last_seen_at" in columns else -1
                if touch_index >= 0:
                    rows = [row[:touch_index] + row[touch_index + 1:] for row in rows]
            content[name] = rows
    return {name: hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            for name, rows in content.items()}


def read_only_call(database_path: Path, base: str, path: str, headers: dict[str, str]) -> dict:
    before = database_snapshot(database_path)
    response = request(base, path, headers=headers)
    after = database_snapshot(database_path)
    if after != before:
        changed = sorted(set(before) | set(after))
        changed = [name for name in changed if before.get(name) != after.get(name)]
        raise FlowError(f"Read-only HTTP path wrote to fixture DB tables: {', '.join(changed)} ({path})")
    return response["data"]


def request(base: str, path: str, *, method: str = "GET", body: dict | None = None,
            headers: dict[str, str] | None = None, expected: tuple[int, ...] = (200,)) -> dict:
    request_headers = {"Accept": "application/json"}
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    req = Request(base + path, data=encoded, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            status, text = response.status, response.read().decode("utf-8")
    except HTTPError as error:
        status, text = error.code, error.read().decode("utf-8")
    except URLError as error:
        raise FlowError(f"Local API unavailable at {base}: {error.reason}") from error
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError as error:
        raise FlowError(f"{method} {path} returned non-JSON response ({status})") from error
    if status not in expected:
        raise FlowError(f"{method} {path}: expected {expected}, received {status}: {payload}")
    return {"status_code": status, "data": payload}


def main() -> None:
    auth = json.loads((DATA / "auth.json").read_text(encoding="utf-8"))
    fixture = json.loads((DATA / "fixture.json").read_text(encoding="utf-8"))
    base = auth["base_url"]
    if base != "http://127.0.0.1:8041":
        raise FlowError("Fixture API base must remain pinned to 127.0.0.1:8041")
    db_path = Path(auth["database"]).resolve()
    if db_path.parent != DATA.resolve():
        raise FlowError("Fixture database must remain inside .local-test/consolidation")
    device_a = {"X-Admin-Browser-Session": auth["admin_browser_session"], "X-School-Admin-Session": auth["admin_browser_session"]}
    device_b = {"X-Admin-Browser-Session": auth["device_b_admin_browser_session"], "X-School-Admin-Session": auth["device_b_admin_browser_session"]}
    admin = {"X-Dev-Auth": "admin:1"}
    student = {"X-Dev-Auth": "student:1"}
    teacher = {"X-Dev-Auth": "teacher:1"}
    results: dict[str, object] = {"synthetic": True, "database": auth["database"]}

    health = request(base, "/health")["data"]
    if health.get("status") != "ok":
        raise FlowError("Backend health did not report ok")
    directory = read_only_call(db_path, base, "/api/admin/school/directory", device_a)
    results["directory_read_only"] = {"classes": len(directory.get("classes") or directory.get("groups") or []),
        "auth_activity_touch": "admin_browser_sessions.last_seen_at"}

    before_active = database_snapshot(db_path)
    active_template = request(base, "/api/admin/schedule/experiments/school/import", method="POST",
                              body={}, headers=device_a)["data"]
    after_active = database_snapshot(db_path)
    if after_active != before_active:
        raise FlowError("Opening the selected school template wrote to the fixture DB")
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        selected_id = connection.execute("SELECT draft_id FROM school_template_selection WHERE scope='school'").fetchone()[0]
    if active_template.get("id") != selected_id:
        raise FlowError("School-template reader disagrees with the explicit current selection")
    results["active_school_template_read_only"] = {"draft_id": active_template["id"],
        "lesson_count": len(active_template["payload"]["lessons"])}

    # Every run uses two new disposable drafts based on the CURRENT published
    # version; it never rebases or overwrites earlier author drafts implicitly.
    baseline = request(base, "/api/admin/schedule/observability?editor=true", headers=admin)["data"]
    fixture["canonical_version_id"] = baseline["canonical"]["version_id"]
    template_item = request(base, "/api/admin/schedule/experiments/school", method="POST", headers=device_a,
        body={"name": "HTTP integration template", "source": "canonical"})["data"]
    fixture["template_publication_draft_id"] = template_item["id"]
    week_item = request(base, f"/api/admin/schedule/experiments/school/{template_item['id']}/week", method="POST", headers=device_a,
        body={"week_start": "2026-10-26", "expected_revision": template_item["revision"]})["data"]
    fixture["week_publication_draft_id"] = week_item["id"]
    results["baseline_template_version_id"] = fixture["canonical_version_id"]

    # Publish the week first so the subsequent template version change proves
    # that weekly effective state remains tied to its own baseline.
    week_id = fixture["week_publication_draft_id"]
    week_url = f"/api/admin/schedule/constructor/{week_id}/publication"
    week_body = {"scope_kind": "week", "week_start": "2026-10-26", "expected_revision": 1}
    before_preview = database_snapshot(db_path)
    week_preview = request(base, week_url + "/preview", method="POST", body=week_body, headers=device_a)["data"]
    after_preview = database_snapshot(db_path)
    if after_preview != before_preview:
        changed = [name for name in sorted(set(before_preview) | set(after_preview))
                   if before_preview.get(name) != after_preview.get(name)]
        raise FlowError(f"Publication preview wrote to the fixture DB tables: {', '.join(changed)}")
    if not week_preview.get("can_publish"):
        raise FlowError(f"Synthetic week preview is blocked: {week_preview.get('blockers')}")
    week_publish_body = {**week_body, "confirmed": True, "preview_token": week_preview["preview_token"]}
    week_published = request(base, week_url + "/publish", method="POST", body=week_publish_body, headers=device_a)["data"]
    stale = request(base, week_url + "/publish", method="POST", body=week_publish_body, headers=device_a, expected=(409,))
    results["week_publication"] = {"published_id": week_published["published_id"],
        "runtime_readback": week_published["runtime_readback"], "repeat_publish_status": stale["status_code"]}

    template_id = fixture["template_publication_draft_id"]
    template_url = f"/api/admin/schedule/constructor/{template_id}/publication"
    template_body = {"scope_kind": "template", "expected_revision": 1}
    template_preview = request(base, template_url + "/preview", method="POST", body=template_body, headers=device_a)["data"]
    if not template_preview.get("can_publish"):
        raise FlowError(f"Synthetic template preview is blocked: {template_preview.get('blockers')}")
    template_published = request(base, template_url + "/publish", method="POST",
        body={**template_body, "confirmed": True, "preview_token": template_preview["preview_token"]}, headers=device_a)["data"]
    results["template_publication"] = {"published_id": template_published["published_id"],
        "runtime_readback": template_published["runtime_readback"]}

    week_status = read_only_call(db_path, base, "/api/admin/schedule/observability?week_start=2026-10-26&editor=true", admin)
    effective_version = (week_status.get("effective_week") or {}).get("version_id")
    current_version = (week_status.get("canonical") or {}).get("version_id")
    if effective_version != fixture["canonical_version_id"] or current_version == fixture["canonical_version_id"]:
        raise FlowError("Published week was not retained independently from the newly published template")
    results["week_survives_template_update"] = {"effective_version_id": effective_version,
        "current_template_version_id": current_version}

    student_id = fixture["students"]["7-А synthetic"][0]
    teacher_id = fixture["teachers"][0]
    student_runtime = request(base, f"/api/student/schedule?{urlencode({'start_day': '2026-10-26', 'end_day': '2026-10-30'})}", headers=student)["data"]
    teacher_runtime = request(base, f"/api/teacher/schedule?{urlencode({'start_day': '2026-10-26', 'end_day': '2026-10-30'})}", headers=teacher)["data"]
    if student_runtime.get("identity_id") != student_id or student_runtime.get("state") != "authoritative":
        raise FlowError("Student HTTP runtime did not read the published effective week")
    if teacher_runtime.get("state") != "authoritative" or not any(
            teacher_id in (item.get("teacher_ids") or []) for item in teacher_runtime.get("items") or []):
        raise FlowError("Teacher HTTP runtime did not read the published effective week")
    results["runtime_http"] = {"student_items": len(student_runtime.get("items") or []),
        "teacher_items": len(teacher_runtime.get("items") or []), "status": "authoritative"}

    draft_inspector = read_only_call(db_path, base,
        f"/api/admin/schedule/experiments/school/{fixture['draft_id']}", device_a)
    results["draft_inspector_read_only"] = {"revision": draft_inspector.get("revision"),
        "lesson_count": len((draft_inspector.get("payload") or {}).get("lessons") or [])}

    colors_path = "/api/admin/school/teacher-ui-colors"
    color_list = request(base, colors_path, headers=device_a)["data"].get("colors") or []
    if len(color_list) != 24:
        raise FlowError(f"Expected 24 persisted synthetic teacher colors, received {len(color_list)}")
    color_id = fixture["teachers"][0]
    color_url = colors_path + "/" + color_id
    initial_color = request(base, color_url, headers=device_a)["data"]
    updated_color = request(base, color_url, method="PUT", body={"color": "#123456", "expected_revision": initial_color["revision"]}, headers=device_a)["data"]
    second_device_color = request(base, color_url, headers=device_b)["data"]
    if second_device_color.get("color") != "#123456" or second_device_color.get("revision") != updated_color.get("revision"):
        raise FlowError("Second synthetic admin session did not read the saved shared color")
    stale_color = request(base, color_url, method="PUT", body={"color": "#ABCDEF", "expected_revision": initial_color["revision"]}, headers=device_b, expected=(409,))
    results["shared_colors"] = {"teacher_count": len(color_list), "updated_revision": updated_color["revision"],
        "second_device_readback": second_device_color["color"], "stale_update_status": stale_color["status_code"]}

    export_result = request(base, f"/api/admin/schedule/experiments/school/{week_id}/export", method="POST",
        body={"expected_revision": 1}, headers=device_a)["data"]
    if export_result.get("status") != "authorization_required":
        raise FlowError("Local export must prepare safely and stop before Google writes")
    results["export_http"] = {"status": export_result["status"], "revision": export_result["revision"],
        "google_writes": 0, "limitation": "Actual Google transport intentionally not authorized; offline plan and fake writer covered separately"}

    before_refresh = database_snapshot(db_path)
    refresh = request(base, "/api/admin/schedule/refresh?week_start=2026-10-26", method="POST", headers=admin)["data"]
    if refresh.get("status") != "manual_publication_protected" or database_snapshot(db_path) != before_refresh:
        raise FlowError("Source refresh did not hold the manual publication without writes")
    results["source_refresh_protection"] = refresh

    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        source_entries = connection.execute("SELECT COUNT(*) FROM schedule_entries").fetchone()[0]
        mappings = connection.execute("SELECT COUNT(*) FROM school_source_mappings").fetchone()[0]
    if source_entries or mappings:
        raise FlowError(f"Fixture publication unexpectedly touched source rows: entries={source_entries}, mappings={mappings}")
    results["source_write_counts"] = {"schedule_entries": source_entries, "school_source_mappings": mappings}

    retired_calls = [
        ("DELETE", "/api/admin/schedule/draft?scope_kind=week&scope_key=2026-10-26&expected_revision=1", None),
        ("PUT", "/api/admin/schedule/draft", {"scope_kind": "template", "scope_key": "school",
          "expected_revision": 1, "payload": {"days": [], "slots": [], "classes": [], "lessons": []}}),
        ("POST", "/api/admin/schedule/draft/publish", {"scope_kind": "template", "scope_key": "school",
          "expected_revision": 1}),
        ("POST", "/api/admin/schedule/template/confirm", {"expected_version_id": fixture["canonical_version_id"]}),
        ("POST", "/api/admin/schedule/import-to-draft", {"week_start": "2026-10-26", "expected_revision": 1,
          "proposed_changes": [], "source_context": {}}),
    ]
    before_retired = database_snapshot(db_path)
    for method, path, body in retired_calls:
        response = request(base, path, method=method, body=body, headers=admin, expected=(410,))
        if response["status_code"] != 410:
            raise FlowError(f"Retired legacy endpoint did not return HTTP 410: {method} {path}")
    after_retired = database_snapshot(db_path)
    if after_retired != before_retired:
        raise FlowError("Retired legacy endpoints mutated the fixture DB")
    results["legacy_schedule_endpoints"] = {"retired": len(retired_calls), "status": 410, "db_writes": 0}

    (ROOT / ".local-test" / "consolidation" / "results").mkdir(parents=True, exist_ok=True)
    (ROOT / ".local-test" / "consolidation" / "results" / "http-integration-report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Local HTTP consolidation flow passed: template/week publication, stale conflict, runtime readback, shared colors, and zero source writes.", flush=True)


if __name__ == "__main__":
    main()
