from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    report_path = Path("docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.json")
    reviewed = json.loads(Path("docs/STUDENT_MEMBERSHIP_DRY_RUN_2026-09-08.json").read_text(encoding="utf-8"))
    final = json.loads(Path("docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.json").read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["post_apply_reconciliation"] = final
    final_by_key = {(str(item.get("identity_id")), str(item.get("group_id"))): item for item in final.get("assignments", []) if item.get("action") == "KEEP"}
    created = list(report["mutations"]["memberships_created"])
    seen = {(str(item.get("identity_id")), str(item.get("group_id"))) for item in created}
    for item in reviewed.get("assignments", []):
        if item.get("action") != "CREATE":
            continue
        key = (str(item.get("identity_id")), str(item.get("group_id")))
        if key in seen:
            continue
        survivor = final_by_key[key]
        created.append({**item, "membership_id": (survivor.get("membership_ids") or [None])[0], "source": "official_import", "source_ref": (survivor.get("source_refs") or [None])[0]})
        seen.add(key)
    report["mutations"]["memberships_created"] = created
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"memberships_created": len(created), "deactivated": len(report["mutations"]["memberships_deactivated"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
