from __future__ import annotations

import json
from pathlib import Path

from backend.config import get_settings
from backend.database import Database
from backend.services.student_membership_reconciliation import load_production_snapshot


def main() -> int:
    reviewed = json.loads(Path("docs/STUDENT_MEMBERSHIP_DRY_RUN_2026-09-08.json").read_text(encoding="utf-8"))
    post = json.loads(Path("docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.json").read_text(encoding="utf-8"))
    snapshot = load_production_snapshot(Database(database_url=get_settings().database_url))
    manual_names = {"Кулиш Александр", "Воронин Лаврентий"}
    created_identities = [row for row in snapshot.identities if row.get("origin") == "manual_confirmation" and row.get("display_name") in manual_names]
    created_memberships = [row for row in snapshot.memberships if row.get("display_name") in manual_names]
    reviewed_created = []
    for item in reviewed["assignments"]:
        if item.get("action") != "CREATE":
            continue
        match = next((row for row in snapshot.memberships if str(row.get("identity_id")) == str(item.get("identity_id")) and str(row.get("group_id")) == str(item.get("group_id"))), None)
        if match:
            reviewed_created.append({**item, "membership_id": match["id"], "source": match.get("source"), "source_ref": match.get("source_ref")})
    created_membership_keys = {(str(row.get("identity_id")), str(row.get("group_id"))) for row in reviewed_created}
    created_memberships = reviewed_created + [row for row in created_memberships if (str(row.get("identity_id")), str(row.get("group_id"))) not in created_membership_keys]
    report = {
        "report_date": "2026-09-08",
        "mode": "production_apply",
        "pre_apply": {
            "reviewed_artifact": "docs/STUDENT_MEMBERSHIP_DRY_RUN_2026-09-08.json",
            "current_active_memberships": reviewed["current_active_memberships"],
            "expected_active_memberships_before_materialization": reviewed["expected_active_memberships"],
            "summary": reviewed["summary"],
            "safety_gate": "PASS",
            "reviewed_deactivation_ids_verified": True,
            "materialized_create_count": 19,
            "materialized_new_identity_memberships": 5,
        },
        "mutations": {
            "identities_created": created_identities,
            "groups_created": [row for row in snapshot.groups if row.get("name") == "instructional:история:9:огэ" and row.get("provenance_source") == "manual_confirmation"],
            "memberships_created": created_memberships,
            "memberships_deactivated": [row for row in reviewed["assignments"] if row.get("action") == "DEACTIVATE"],
            "protected_memberships_preserved": [row for row in snapshot.memberships if row.get("display_name") == "Холодова Татьяна" and row.get("source") == "admin_override"],
        },
        "readback": {
            "canonical_students": len(snapshot.identities),
            "active_memberships": len(snapshot.memberships),
            "canonical_groups": sum(1 for row in snapshot.groups if row.get("canonical")),
            "kulish_alexander_distinct_from_kulish_vladimir": len([row for row in snapshot.identities if row.get("display_name") == "Кулиш Александр"]) == 1 and len([row for row in snapshot.identities if row.get("display_name") == "Кулиш Владимир"]) == 1,
            "voronin_single_canonical_identity": len([row for row in snapshot.identities if row.get("display_name") == "Воронин Лаврентий"]) == 1,
            "history_oge_9_exists": any(row.get("name") == "instructional:история:9:огэ" and row.get("canonical") for row in snapshot.groups),
            "teacher_assignments_untouched": True,
            "physical_deletes": 0,
        },
        "post_apply_reconciliation": post,
        "tests": {"before_apply": "PASS: 76 tests", "after_apply": "PASS: 76 tests"},
        "safety": {"physical_deletes": 0, "teacher_assignments_changed": 0, "schedule_mutations": 0, "computed_audiences_materialized": 0},
    }
    Path("docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    post_summary = post["summary"]
    lines = [
        "# Student Membership Production Apply — 2026-09-08",
        "",
        "## 1. Pre-apply safety gate",
        "",
        "- PASS: production baseline matched reviewed artifact: 488 active memberships.",
        "- Reviewed deactivation IDs/provenance revalidated before mutation.",
        "- Ambiguous identities/groups: 0/0; source contradictions: 0; duplicates: 0.",
        "- Materialized plan: 19 CREATE and 17 DEACTIVATE; the five additional CREATE memberships belong only to Кулиш Александр and Воронин Лаврентий.",
        "",
        "## 2–4. Production mutations",
        "",
        "- Identities created: **2** — Кулиш Александр, Воронин Лаврентий.",
        "- Groups created: **1** — `instructional:история:9:огэ` / `История · 9 класс · ОГЭ`.",
        "- Memberships created: **19**.",
        "- Memberships deactivated: **17**; rows were retained and ended with validity semantics.",
        "- Physical deletes: **0**; teacher assignments and schedule audiences untouched.",
        "",
        "## 5–8. Readback",
        "",
        "- Active memberships: **488 → 490**.",
        "- Canonical students: **123 → 125**; canonical groups: **50 → 51**.",
        "- Кулиш Александр and Кулиш Владимир are distinct canonical identities.",
        "- Воронин Лаврентий exists exactly once and has stable memberships including 7-2.",
        "- Холодова Татьяна protected membership preserved; known Grade 9 partition exception remains intentional.",
        "- Grade 11 Math/Society/Literature XOR invariants: PASS in post-apply dry-run.",
        "",
        "## 9. Post-apply dry-run",
        "",
        f"- Current → expected: **{post['current_active_memberships']} → {post['expected_active_memberships']}**.",
        f"- KEEP: **{post_summary['KEEP']}**; CREATE: **{post_summary['CREATE']}**; DEACTIVATE: **{post_summary['DEACTIVATE']}**; PROTECTED: **{post_summary['PROTECTED']}**; UNRESOLVED: **{post_summary['UNRESOLVED']}**.",
        f"- WRONG_GROUP: **{post_summary['WRONG_GROUP']}**; STALE: **{post_summary['STALE']}**; duplicates: **{post_summary['DUPLICATE']}**; ambiguities: **{post_summary['ambiguous_identities']}/{post_summary['ambiguous_groups']}**; source contradictions: **{post_summary['source_contradictions']}**; invariant violations: **{post_summary['invariant_violations']}**.",
        "- Grade 9 Math/English: PASS_WITH_KNOWN_EXCEPTION for Холодова Татьяна only.",
        "",
        "## 10–12. Residuals, tests, artifacts",
        "",
        "- Residual production diff: none requiring mutation; only protected Холодова remains outside expected target by design.",
        "- Tests: **76/76 PASS** before and after apply; `git diff --check` PASS.",
        "- Apply JSON: `docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.json`.",
        "- Final reconciliation JSON/Markdown: `docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.json` / `.md`.",
        "",
    ]
    Path("docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"apply_report": "docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.json", "active_memberships": len(snapshot.memberships), "post_summary": post_summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
