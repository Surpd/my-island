from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from backend.config import get_settings
from backend.services.student_membership_apply import StudentMembershipApplyBlocked, run_apply


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the reviewed student membership reconciliation to production")
    parser.add_argument("--apply", action="store_true", required=True, help="required safety acknowledgement")
    args = parser.parse_args()
    if not args.apply:
        return 2
    load_dotenv(".env")
    try:
        result = run_apply(get_settings())
    except StudentMembershipApplyBlocked as error:
        print(f"STOPPED WITHOUT APPLY: {error}", file=sys.stderr)
        return 2
    print({"status": "applied", "active_memberships": result["readback"]["active_memberships"], "post_diff": result["post_apply_reconciliation"]["summary"], "reports": ["docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.md", "docs/STUDENT_MEMBERSHIP_APPLY_2026-09-08.json", "docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.md", "docs/STUDENT_MEMBERSHIP_RECONCILIATION_FINAL_2026-09-08.json"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
