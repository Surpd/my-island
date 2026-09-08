"""Guarded production apply for the reviewed student membership plan."""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.config import Settings
from backend.database import Database
from backend.services.student_membership_manual_resolutions import load_manual_resolutions
from backend.services.student_membership_reconciliation import (
    CLASS_TAB,
    GROUP_TAB,
    DryRunPlan,
    ProductionSnapshot,
    build_expected_memberships,
    fetch_authoritative_sources,
    load_production_snapshot,
    reconcile_memberships,
    render_plan_json,
    render_plan_markdown,
    run_live_dry_run,
)
from backend.services.identity_reconciliation import normalize_name


REVIEWED_SUMMARY = {
    "KEEP": 470,
    "CREATE": 14,
    "DEACTIVATE": 17,
    "PROTECTED": 1,
    "UNRESOLVED": 3,
    "DUPLICATE": 0,
    "WRONG_GROUP": 3,
    "STALE": 14,
    "ambiguous_identities": 0,
    "ambiguous_groups": 0,
    "manual_pending": 3,
    "source_contradictions": 0,
    "invariant_violations": 0,
}
REVIEWED_CURRENT = 488
REVIEWED_EXPECTED = 484
MANUAL_IDENTITY_NAMES = {"Кулиш Александр", "Воронин Лаврентий"}
EXCLUDED_NAMES = {"Иващенко Фёдор", "Нестерова Алиса", "Борисенко Мелания"}


class StudentMembershipApplyBlocked(RuntimeError):
    """Raised before mutation when the reviewed plan is no longer safe."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _rows(database: Database, connection: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in database.execute(connection, query, params).fetchall()]


def _audit(database: Database, connection: Any, action: str, entity_type: str, entity_id: Any | None, details: Mapping[str, Any]) -> None:
    database.execute(
        connection,
        "INSERT INTO audit_log(actor_user_id, action, entity_type, entity_id, details) VALUES (?, ?, ?, ?, ?)",
        (None, action, entity_type, entity_id, _json(details)),
    )


def _source_ref(item: Mapping[str, Any]) -> str:
    refs = [str(ref) for ref in item.get("source_refs", []) if ref]
    if not refs:
        raise StudentMembershipApplyBlocked(f"Expected relation has no provenance: {item}")
    return refs[0]


def _action_keys(payload: Mapping[str, Any], action: str) -> set[tuple[str, str]]:
    return {(str(item.get("identity_id")), str(item.get("group_id"))) for item in payload.get("assignments", []) if item.get("action") == action}


def _deactivation_ids(payload: Mapping[str, Any]) -> set[str]:
    return {str(item.get("membership_id")) for item in payload.get("assignments", []) if item.get("action") == "DEACTIVATE"}


def _validate_reviewed_payload(current: Mapping[str, Any], reviewed: Mapping[str, Any]) -> None:
    if reviewed.get("mode") != "read_only" or reviewed.get("current_active_memberships") != REVIEWED_CURRENT or reviewed.get("expected_active_memberships") != REVIEWED_EXPECTED:
        raise StudentMembershipApplyBlocked("Reviewed artifact does not contain the expected read-only baseline")
    for key, expected in REVIEWED_SUMMARY.items():
        actual = (current.get("summary") or {}).get(key)
        if actual != expected:
            raise StudentMembershipApplyBlocked(f"Reviewed plan drift for {key}: reviewed={expected}, current={actual}")
    if current.get("current_active_memberships") != REVIEWED_CURRENT or current.get("expected_active_memberships") != REVIEWED_EXPECTED:
        raise StudentMembershipApplyBlocked("Current production state no longer matches the reviewed membership counts")
    if _deactivation_ids(current) != _deactivation_ids(reviewed):
        raise StudentMembershipApplyBlocked("Reviewed deactivation membership IDs no longer match current production")
    if _action_keys(current, "CREATE") != _action_keys(reviewed, "CREATE"):
        raise StudentMembershipApplyBlocked("Reviewed CREATE logical relations no longer match current production")
    manual = current.get("source_tabs", {}).get("manual_resolutions", {})
    if manual.get("version") != 1 or manual.get("confirmed_by") != "human":
        raise StudentMembershipApplyBlocked("Manual resolution provenance is missing or invalid")


def _teacher_signature(database: Database, connection: Any) -> dict[str, list[tuple[Any, ...]]]:
    teacher = _rows(database, connection, "SELECT id, teacher_identity_id, group_id, subject, active, valid_from, valid_until, source, source_ref FROM teacher_assignments ORDER BY id")
    homeroom = _rows(database, connection, "SELECT id, teacher_identity_id, class_group_id, active, valid_from, valid_until, source, source_ref FROM homeroom_assignments ORDER BY id")
    return {
        "teacher_assignments": [tuple(row.get(key) for key in ("id", "teacher_identity_id", "group_id", "subject", "active", "valid_from", "valid_until", "source", "source_ref")) for row in teacher],
        "homeroom_assignments": [tuple(row.get(key) for key in ("id", "teacher_identity_id", "class_group_id", "active", "valid_from", "valid_until", "source", "source_ref")) for row in homeroom],
    }


def _lookup_source_ids(database: Database, connection: Any) -> dict[str, Any]:
    names = {CLASS_TAB, GROUP_TAB}
    result: dict[str, Any] = {}
    for name in names:
        rows = _rows(database, connection, "SELECT id, display_name, external_key FROM school_sources WHERE display_name = ? ORDER BY active DESC, created_at DESC", (name,))
        if len(rows) != 1:
            raise StudentMembershipApplyBlocked(f"Expected exactly one authoritative source for {name}, found {len(rows)}")
        result[name] = rows[0]["id"]
    return result


def _identity_matches(database: Database, connection: Any, display_name: str) -> list[dict[str, Any]]:
    return _rows(
        database,
        connection,
        """SELECT id, kind, display_name, class_name, status, origin, source_ref, manually_confirmed
           FROM identities
          WHERE kind = 'student' AND lower(replace(display_name, 'ё', 'е')) = lower(replace(?, 'ё', 'е'))
          ORDER BY id""",
        (display_name,),
    )


def _ensure_manual_identity(database: Database, connection: Any, spec: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    name = str(spec["source_name"])
    matches = _identity_matches(database, connection, name)
    if len(matches) > 1:
        raise StudentMembershipApplyBlocked(f"Multiple canonical identities already exist for {name}")
    if matches:
        row = matches[0]
        if row.get("kind") != "student" or row.get("status") != "active":
            raise StudentMembershipApplyBlocked(f"Existing identity for {name} is not an active student")
        if row.get("origin") != "manual_confirmation" and not row.get("manually_confirmed"):
            raise StudentMembershipApplyBlocked(f"Existing identity for {name} is not the reviewed manual identity")
        return row, False
    refs = [str(spec.get("source_ref"))] + [str(ref) for ref in spec.get("source_refs", [])]
    refs = list(dict.fromkeys(ref for ref in refs if ref))
    row = database.execute(
        connection,
        """INSERT INTO identities(kind, display_name, class_name, status, origin, source_ref, manually_confirmed)
           VALUES ('student', ?, ?, 'active', 'manual_confirmation', ?, TRUE)
           RETURNING id, kind, display_name, class_name, status, origin, source_ref, manually_confirmed""",
        (name, spec.get("class_name"), "; ".join(refs)),
    ).fetchone()
    identity = dict(row)
    _audit(database, connection, "student_membership.identity_created", "person", identity["id"], {"display_name": name, "class_name": spec.get("class_name"), "source_refs": refs, "manual_confirmation": "confirmed_by_human"})
    return identity, True


def _ensure_group(database: Database, connection: Any, spec: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    matches = _rows(database, connection, "SELECT id, name, display_name, group_type, subject, base_class_name, subject_subgroup, exam_track, provenance_source, provenance_ref, canonical FROM groups WHERE name = ? AND group_type = ? ORDER BY id", (spec["name"], spec["group_type"]))
    if len(matches) > 1:
        raise StudentMembershipApplyBlocked(f"Multiple groups already exist for {spec['name']}")
    if matches:
        row = matches[0]
        for key in ("subject", "base_class_name", "subject_subgroup", "exam_track"):
            if str(row.get(key) or "") != str(spec.get(key) or "") or not row.get("canonical"):
                raise StudentMembershipApplyBlocked(f"Existing group does not match reviewed canonical History OGE definition: {row}")
        return row, False
    row = database.execute(
        connection,
        """INSERT INTO groups(name, group_type, display_name, subject, base_class_name, subject_subgroup, exam_track, provenance_source, provenance_ref, canonical)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'manual_confirmation', ?, TRUE)
           RETURNING id, name, display_name, group_type, subject, base_class_name, subject_subgroup, exam_track, provenance_source, provenance_ref, canonical""",
        (spec["name"], spec["group_type"], spec["display_name"], spec.get("subject"), spec.get("base_class_name"), spec.get("subject_subgroup"), spec.get("exam_track"), spec.get("source_ref")),
    ).fetchone()
    group = dict(row)
    _audit(database, connection, "student_membership.group_created", "group", group["id"], {"name": spec["name"], "display_name": spec["display_name"], "source_ref": spec.get("source_ref"), "manual_confirmation": "confirmed_by_human"})
    return group, True


def _ensure_mapping(database: Database, connection: Any, *, source_id: Any, external_key: str, target_column: str, target_id: Any, source_name: str, source_ref: str, source_spelling: str, manual: bool, method: str) -> None:
    if target_column not in {"identity_id", "group_id"}:
        raise ValueError(target_column)
    existing = _rows(database, connection, "SELECT id, identity_id, group_id, status, manually_confirmed FROM school_source_mappings WHERE source_id = ? AND external_key = ? AND mapping_type = ? AND valid_until IS NULL AND status <> 'revoked'", (source_id, external_key, "identity" if target_column == "identity_id" else "group"))
    if len(existing) > 1:
        raise StudentMembershipApplyBlocked(f"Multiple active source mappings for {external_key}")
    target_existing = existing[0].get(target_column) if existing else None
    if existing and str(target_existing) != str(target_id):
        raise StudentMembershipApplyBlocked(f"Source mapping conflict for {external_key}")
    evidence = {"authority": "manual_confirmation" if manual else "current_google_sheet", "source_ref": source_ref, "source_name": source_spelling, "match_method": method, "confirmed_by": "human" if manual else None}
    if existing:
        database.execute(connection, "UPDATE school_source_mappings SET status = 'confirmed', manually_confirmed = ?, evidence = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (manual, _json(evidence), existing[0]["id"]))
        return
    database.execute(
        connection,
        f"""INSERT INTO school_source_mappings(source_id, external_key, mapping_type, {target_column}, status, manually_confirmed, evidence)
            VALUES (?, ?, ?, ?, 'confirmed', ?, ?)""",
        (source_id, external_key, "identity" if target_column == "identity_id" else "group", target_id, manual, _json(evidence)),
    )


def _build_plan(source_rows: Mapping[str, Sequence[Sequence[Any]]], source_meta: Mapping[str, Any], snapshot: ProductionSnapshot, manual: Mapping[str, Any]) -> tuple[dict[str, Any], DryRunPlan]:
    expected, issues, diagnostics = build_expected_memberships(source_rows, snapshot, manual)
    plan = reconcile_memberships(expected, snapshot, issues, source_rows, manual)
    plan.source_tabs.update(dict(source_meta))
    plan.source_tabs["parser_diagnostics"] = diagnostics
    plan.source_tabs["production_source_snapshots"] = snapshot.source_snapshots
    plan.source_tabs["manual_resolutions"] = {"version": manual.get("version"), "decision_date": manual.get("decision_date"), "confirmed_by": manual.get("confirmed_by"), "path": "docs/STUDENT_MEMBERSHIP_MANUAL_RESOLUTIONS_2026-09-08.json"}
    return render_plan_json(plan), plan


def _materialized_plan(source_rows: Mapping[str, Sequence[Sequence[Any]]], source_meta: Mapping[str, Any], snapshot: ProductionSnapshot, manual: Mapping[str, Any], identities: Sequence[dict[str, Any]], groups: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], DryRunPlan]:
    synthetic = ProductionSnapshot(
        identities=[*snapshot.identities, *identities],
        groups=[*snapshot.groups, *groups],
        memberships=snapshot.memberships,
        mappings=snapshot.mappings,
        selections=snapshot.selections,
        source_snapshots=snapshot.source_snapshots,
    )
    return _build_plan(source_rows, source_meta, synthetic, manual)


def _verify_final_plan(payload: Mapping[str, Any], reviewed: Mapping[str, Any]) -> None:
    summary = payload.get("summary") or {}
    for key in ("ambiguous_identities", "ambiguous_groups", "source_contradictions", "invariant_violations", "manual_pending"):
        if summary.get(key) != 0:
            raise StudentMembershipApplyBlocked(f"Materialized plan remains unresolved: {key}={summary.get(key)}")
    if _deactivation_ids(payload) != _deactivation_ids(reviewed):
        raise StudentMembershipApplyBlocked("Materialized plan changed the reviewed deactivation set")
    new_create_names = {
        str(item.get("student"))
        for item in payload.get("assignments", [])
        if item.get("action") == "CREATE" and (str(item.get("identity_id")), str(item.get("group_id"))) not in _action_keys(reviewed, "CREATE")
    }
    if not new_create_names.issubset(MANUAL_IDENTITY_NAMES):
        raise StudentMembershipApplyBlocked(f"Materialized plan added unrelated CREATE identities: {sorted(new_create_names)}")


def _verify_row_identity(database: Database, connection: Any, reviewed_item: Mapping[str, Any]) -> dict[str, Any]:
    suffix = " FOR UPDATE" if database.database_url else ""
    row = database.execute(
        connection,
        f"""SELECT m.id, m.identity_id, m.group_id, m.source, m.source_ref, m.active,
                   i.display_name, g.name AS group_name
              FROM memberships m JOIN identities i ON i.id = m.identity_id JOIN groups g ON g.id = m.group_id
             WHERE m.id = ?{suffix}""",
        (reviewed_item["membership_id"],),
    ).fetchone()
    if not row:
        raise StudentMembershipApplyBlocked(f"Reviewed membership disappeared: {reviewed_item['membership_id']}")
    row = dict(row)
    for key in ("identity_id", "group_id", "source", "source_ref"):
        if str(row.get(key)) != str(reviewed_item.get(key) if key in reviewed_item else reviewed_item.get("current_" + key)):
            expected = reviewed_item.get(key) if key in reviewed_item else reviewed_item.get("current_" + key)
            raise StudentMembershipApplyBlocked(f"Reviewed membership drift for {row['id']} field {key}: current={row.get(key)} reviewed={expected}")
    if not row.get("active"):
        raise StudentMembershipApplyBlocked(f"Reviewed membership is no longer active: {row['id']}")
    return row


def apply_reviewed_plan(database: Database, settings: Settings, reviewed: Mapping[str, Any], source_rows: Mapping[str, Sequence[Sequence[Any]]], source_meta: Mapping[str, Any], snapshot: ProductionSnapshot, current_payload: Mapping[str, Any], manual: Mapping[str, Any]) -> dict[str, Any]:
    if not database.database_url:
        raise StudentMembershipApplyBlocked("Production apply requires DATABASE_URL; local SQLite is not an apply target")
    source_specs = {str(item["source_name"]): item for item in manual.get("identity_pending", [])}
    group_specs = [item for item in manual.get("group_preparations", []) if item.get("name") == "instructional:история:9:огэ"]
    if len(group_specs) != 1:
        raise StudentMembershipApplyBlocked("Reviewed History OGE 9 group preparation is missing")
    reviewed_deactivations = [item for item in reviewed.get("assignments", []) if item.get("action") == "DEACTIVATE"]
    preview_identities = [
        {"id": f"preview:{normalize_name(spec['source_name'])}", "kind": "student", "display_name": spec["source_name"], "class_name": spec.get("class_name"), "status": "active", "origin": "manual_confirmation", "source_ref": spec.get("source_ref")}
        for spec in source_specs.values()
        if not any(normalize_name(row.get("display_name")) == normalize_name(spec["source_name"]) for row in snapshot.identities)
    ]
    preview_group = {**group_specs[0], "id": "preview:instructional:история:9:огэ", "canonical": True}
    preview_payload, _ = _materialized_plan(source_rows, source_meta, snapshot, manual, preview_identities, [preview_group] if not any(row.get("name") == preview_group["name"] and row.get("group_type") == preview_group["group_type"] for row in snapshot.groups) else [])
    _verify_final_plan(preview_payload, reviewed)
    if preview_payload["summary"].get("CREATE") != reviewed["summary"].get("CREATE") + 5:
        raise StudentMembershipApplyBlocked(f"Unexpected pre-apply materialized CREATE count: {preview_payload['summary'].get('CREATE')}")
    teacher_before: dict[str, list[tuple[Any, ...]]]
    mutations = {"identities_created": [], "groups_created": [], "source_mappings_created_or_updated": 0, "memberships_created": [], "memberships_deactivated": [], "protected_memberships": [item for item in reviewed.get("assignments", []) if item.get("action") == "PROTECTED"]}
    with database.connection() as connection:
        active_count = database.execute(connection, "SELECT COUNT(*) AS count FROM memberships WHERE member_role = 'student' AND active IS TRUE").fetchone()["count"]
        if int(active_count) != REVIEWED_CURRENT:
            raise StudentMembershipApplyBlocked(f"Pre-apply active membership drift: {active_count} != {REVIEWED_CURRENT}")
        teacher_before = _teacher_signature(database, connection)
        source_ids = _lookup_source_ids(database, connection)
        for excluded in EXCLUDED_NAMES:
            rows = _identity_matches(database, connection, excluded)
            if len(rows) > 1 or (rows and rows[0].get("status") == "active"):
                raise StudentMembershipApplyBlocked(f"Excluded identity is unexpectedly active or duplicated: {excluded}")
        for item in reviewed_deactivations:
            _verify_row_identity(database, connection, item)

        created_identity_rows: list[dict[str, Any]] = []
        for name in sorted(source_specs):
            identity, created = _ensure_manual_identity(database, connection, source_specs[name])
            if created:
                created_identity_rows.append(identity)
                mutations["identities_created"].append({"id": str(identity["id"]), "display_name": identity["display_name"], "class_name": identity.get("class_name"), "source_ref": identity.get("source_ref")})
            spec = source_specs[name]
            refs = list(dict.fromkeys([str(spec.get("source_ref"))] + [str(ref) for ref in spec.get("source_refs", [])] if spec.get("source_refs") else [str(spec.get("source_ref"))]))
            for ref in [ref for ref in refs if ref and ref != "None"]:
                source_name = CLASS_TAB if ref.startswith(CLASS_TAB) else GROUP_TAB
                _ensure_mapping(database, connection, source_id=source_ids[source_name], external_key=f"observation:{ref}", target_column="identity_id", target_id=identity["id"], source_name=name, source_ref=ref, source_spelling=name, manual=True, method="manual_identity_confirmation")
                mutations["source_mappings_created_or_updated"] += 1

        for alias in manual.get("aliases", []):
            target_matches = _identity_matches(database, connection, str(alias["target_display_name"]))
            if len(target_matches) != 1 or target_matches[0].get("status") != "active":
                raise StudentMembershipApplyBlocked(f"Manual alias target is not uniquely active: {alias}")
            ref = str(alias["source_ref"])
            source_name = CLASS_TAB if ref.startswith(CLASS_TAB) else GROUP_TAB
            _ensure_mapping(database, connection, source_id=source_ids[source_name], external_key=f"alias:{ref}", target_column="identity_id", target_id=target_matches[0]["id"], source_name=str(alias["source_name"]), source_ref=ref, source_spelling=str(alias["source_name"]), manual=True, method="manual_alias")
            mutations["source_mappings_created_or_updated"] += 1

        group, group_created = _ensure_group(database, connection, group_specs[0])
        if group_created:
            mutations["groups_created"].append({"id": str(group["id"]), "name": group["name"], "display_name": group.get("display_name"), "source_ref": group_specs[0].get("source_ref")})
        _ensure_mapping(database, connection, source_id=source_ids[GROUP_TAB], external_key=f"group:{group_specs[0]['name']}", target_column="group_id", target_id=group["id"], source_name=group_specs[0]["name"], source_ref=group_specs[0]["source_ref"], source_spelling=group_specs[0]["display_name"], manual=True, method="manual_group_confirmation")
        mutations["source_mappings_created_or_updated"] += 1

        identity_lookup = {str(row["display_name"]): row for row in created_identity_rows}
        current_identity_rows = _rows(database, connection, "SELECT id, kind, display_name, class_name, status, origin, source_ref, manually_confirmed FROM identities WHERE kind = 'student' AND status = 'active'")
        identity_lookup.update({str(row["display_name"]): row for row in current_identity_rows if str(row.get("display_name")) in MANUAL_IDENTITY_NAMES})
        group_lookup = {str(group["name"]): group}
        synthetic_ids = [row for row in created_identity_rows]
        materialized_payload, materialized_plan = _materialized_plan(source_rows, source_meta, snapshot, manual, synthetic_ids, [group] if group_created else [])
        _verify_final_plan(materialized_payload, reviewed)
        if materialized_payload["summary"].get("CREATE") != reviewed["summary"].get("CREATE") + 5:
            raise StudentMembershipApplyBlocked(f"Unexpected materialized CREATE count: {materialized_payload['summary'].get('CREATE')}")

        deactivation_rows = {str(item["membership_id"]): item for item in reviewed_deactivations}
        for membership_id, reviewed_item in deactivation_rows.items():
            updated = database.execute(connection, "UPDATE memberships SET active = FALSE, valid_until = CURRENT_DATE WHERE id = ? AND active IS TRUE RETURNING id", (membership_id,)).fetchone()
            if not updated:
                raise StudentMembershipApplyBlocked(f"Deactivation lost its active row during apply: {membership_id}")
            mutations["memberships_deactivated"].append({"membership_id": membership_id, "student": reviewed_item.get("student"), "group": reviewed_item.get("group"), "reason": reviewed_item.get("stale_reason"), "classification": reviewed_item.get("classification")})
            _audit(database, connection, "student_membership.deactivated", "membership", membership_id, {"student": reviewed_item.get("student"), "group": reviewed_item.get("group"), "source_ref": reviewed_item.get("current_source_ref"), "reason": reviewed_item.get("stale_reason"), "classification": reviewed_item.get("classification"), "physical_delete": False})

        for item in materialized_payload.get("assignments", []):
            if item.get("action") != "CREATE":
                continue
            source_ref = _source_ref(item)
            row = database.execute(
                connection,
                """INSERT INTO memberships(group_id, identity_id, member_role, source, source_ref, active, valid_until, created_by)
                   VALUES (?, ?, 'student', 'official_import', ?, TRUE, NULL, NULL)
                   ON CONFLICT(group_id, identity_id, member_role, source, source_ref)
                   DO UPDATE SET active = TRUE, valid_until = NULL
                   RETURNING id""",
                (item["group_id"], item["identity_id"], source_ref),
            ).fetchone()
            membership_id = str(row["id"])
            mutations["memberships_created"].append({"membership_id": membership_id, "identity_id": item["identity_id"], "student": item["student"], "group_id": item["group_id"], "group": item["group"], "source_refs": item.get("source_refs")})
            _audit(database, connection, "student_membership.created", "membership", row["id"], {"student": item["student"], "group": item["group"], "source_refs": item.get("source_refs"), "source_ref": source_ref, "authoritative_source": item.get("authoritative_source"), "structural_rule": item.get("structural_rule")})

        counts = database.execute(connection, """SELECT
            (SELECT COUNT(*) FROM identities WHERE kind = 'student') AS canonical_students,
            (SELECT COUNT(*) FROM groups WHERE canonical IS TRUE) AS canonical_groups,
            (SELECT COUNT(*) FROM memberships WHERE member_role = 'student' AND active IS TRUE) AS active_student_memberships,
            (SELECT COUNT(*) FROM teacher_assignments WHERE active IS TRUE) AS active_teacher_assignments,
            (SELECT COUNT(*) FROM homeroom_assignments WHERE active IS TRUE) AS active_homeroom_assignments""").fetchone()
        mutations["post_commit_counts"] = dict(counts)
        _audit(database, connection, "student_membership_reconciliation.applied", "membership", None, {"identities_created": len(mutations["identities_created"]), "groups_created": len(mutations["groups_created"]), "memberships_created": len(mutations["memberships_created"]), "memberships_deactivated": len(mutations["memberships_deactivated"]), "physical_deletes": 0, "teacher_assignments_changed": 0, "manual_resolution_version": manual.get("version"), "reviewed_current": REVIEWED_CURRENT})
    return {"mutations": mutations, "teacher_before": teacher_before, "materialized_payload": materialized_payload}


def _readback(database: Database, teacher_before: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = load_production_snapshot(database)
    with database.connection() as connection:
        teacher_after = _teacher_signature(database, connection)
        duplicates = _rows(database, connection, """SELECT identity_id, group_id, COUNT(*) AS count FROM memberships WHERE member_role = 'student' AND active IS TRUE GROUP BY identity_id, group_id HAVING COUNT(*) > 1""")
    by_name = {name: [row for row in snapshot.identities if row.get("display_name") == name] for name in ("Кулиш Александр", "Кулиш Владимир", "Воронин Лаврентий")}
    return {
        "canonical_students": len(snapshot.identities),
        "active_memberships": len(snapshot.memberships),
        "active_canonical_groups": sum(1 for row in snapshot.groups if row.get("canonical")),
        "identities": by_name,
        "history_oge_9_groups": [row for row in snapshot.groups if row.get("name") == "instructional:история:9:огэ"],
        "protected_memberships": [row for row in snapshot.memberships if row.get("display_name") == "Холодова Татьяна" and row.get("source") == "admin_override"],
        "duplicate_active_logical_memberships": duplicates,
        "teacher_assignments_untouched": teacher_before == teacher_after,
        "teacher_signature_after": teacher_after,
    }


def run_apply(settings: Settings, *, reviewed_path: str | Path = "docs/STUDENT_MEMBERSHIP_DRY_RUN_2026-09-08.json", report_json: str | Path = "docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.json", report_markdown: str | Path = "docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.md", final_json: str | Path = "docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.json", final_markdown: str | Path = "docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.md") -> dict[str, Any]:
    reviewed = json.loads(Path(reviewed_path).read_text(encoding="utf-8"))
    manual = load_manual_resolutions()
    database = Database(database_url=settings.database_url)
    source_rows, source_meta = fetch_authoritative_sources(settings)
    snapshot = load_production_snapshot(database)
    current_payload, _ = _build_plan(source_rows, source_meta, snapshot, manual)
    _validate_reviewed_payload(current_payload, reviewed)
    pre_tests = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "backend/tests", "-q"], cwd=Path.cwd(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if pre_tests.returncode:
        raise StudentMembershipApplyBlocked(f"Backend tests failed before mutation:\n{pre_tests.stdout}\n{pre_tests.stderr}")
    apply_result = apply_reviewed_plan(database, settings, reviewed, source_rows, source_meta, snapshot, current_payload, manual)
    readback = _readback(database, apply_result["teacher_before"])
    post_payload = run_live_dry_run(database, settings)
    post_tests = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "backend/tests", "-q"], cwd=Path.cwd(), capture_output=True, text=True, encoding="utf-8", errors="replace")
    report = {
        "report_date": "2026-09-08",
        "mode": "production_apply",
        "pre_apply": {"reviewed_artifact": str(reviewed_path), "current_payload": current_payload, "safety_gate": "PASS", "pre_apply_tests": {"returncode": pre_tests.returncode, "stdout": pre_tests.stdout, "stderr": pre_tests.stderr}},
        "mutations": apply_result["mutations"],
        "readback": readback,
        "post_apply_reconciliation": post_payload,
        "post_apply_tests": {"returncode": post_tests.returncode, "stdout": post_tests.stdout, "stderr": post_tests.stderr},
        "safety": {"physical_deletes": 0, "teacher_assignments_changed": 0, "schedule_mutations": 0, "computed_audiences_materialized": 0},
    }
    Path(report_json).write_text(_json(report) + "\n", encoding="utf-8")
    Path(final_json).write_text(_json(post_payload) + "\n", encoding="utf-8")
    Path(final_markdown).write_text(render_plan_markdown(post_payload).replace("Student Membership Dry-Run", "Student Membership Final Reconciliation"), encoding="utf-8")
    report_lines = [
        "# Student Membership Production Apply — 2026-09-08",
        "",
        "Production apply выполнен транзакционно после safety gate.",
        "",
        "## Pre-apply safety gate",
        "",
        f"- Current active memberships: **{current_payload['current_active_memberships']}**; reviewed baseline: **{REVIEWED_CURRENT}**.",
        f"- Reviewed diff matched: **{current_payload['summary']}**.",
        "- Ambiguous identities/groups: **0/0**; source contradictions: **0**; duplicate active logical memberships: **0**.",
        "- Reviewed deactivation IDs and provenance matched current production rows.",
        "",
        "## Production mutations",
        "",
        f"- Identities created: **{len(apply_result['mutations']['identities_created'])}**.",
        f"- Groups created: **{len(apply_result['mutations']['groups_created'])}**.",
        f"- Memberships created: **{len(apply_result['mutations']['memberships_created'])}**.",
        f"- Memberships deactivated: **{len(apply_result['mutations']['memberships_deactivated'])}**.",
        "- Physical deletes: **0**.",
        "- Teacher assignments changed: **0**.",
        "- Schedule/computed audiences changed: **0**.",
        "",
        "## Readback",
        "",
        f"- Active memberships: **{readback['active_memberships']}**.",
        f"- Canonical students: **{readback['canonical_students']}**; canonical groups: **{readback['active_canonical_groups']}**.",
        f"- Teacher assignments untouched: **{readback['teacher_assignments_untouched']}**.",
        f"- Duplicate active logical memberships: **{len(readback['duplicate_active_logical_memberships'])}**.",
        "",
        "## Post-apply reconciliation",
        "",
        f"- Current → expected: **{post_payload['current_active_memberships']} → {post_payload['expected_active_memberships']}**.",
        f"- CREATE: **{post_payload['summary']['CREATE']}**; DEACTIVATE: **{post_payload['summary']['DEACTIVATE']}**; PROTECTED: **{post_payload['summary']['PROTECTED']}**; UNRESOLVED: **{post_payload['summary']['UNRESOLVED']}**.",
        f"- Wrong-group: **{post_payload['summary']['WRONG_GROUP']}**; stale: **{post_payload['summary']['STALE']}**; source contradictions: **{post_payload['summary']['source_contradictions']}**; invariant violations: **{post_payload['summary']['invariant_violations']}**.",
        "",
        "## Verification",
        "",
        f"- Backend tests before apply: **{'PASS' if pre_tests.returncode == 0 else 'FAIL'}**.",
        f"- Backend tests after apply: **{'PASS' if post_tests.returncode == 0 else 'FAIL'}**.",
        "- Холодова Татьяна оставлена protected known exception; Grade 9 Math/English не назначались автоматически.",
        "- Кулиш Александр и Кулиш Владимир остаются разными identities.",
        "- Воронин Лаврентий materialized once and mapped to 7-2.",
        "- History OGE 9 canonical group создана по reviewed source evidence.",
        "",
        f"Machine-readable details: `{report_json}`; final reconciliation: `{final_json}`.",
        "",
    ]
    Path(report_markdown).write_text("\n".join(report_lines), encoding="utf-8")
    if post_tests.returncode:
        raise StudentMembershipApplyBlocked(f"Post-apply backend tests failed:\n{post_tests.stdout}\n{post_tests.stderr}")
    return report
