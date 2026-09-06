from __future__ import annotations

from backend.database import Database


def claim_identity(database: Database, user_id: int, identity_id: int, role: str):
    if role not in {"student", "teacher"}:
        raise ValueError("Only student or teacher identities can be claimed")
    return database.create_claim(user_id, identity_id, role)


def review_identity_claim(database: Database, claim_id: int, reviewer_id: int, status: str):
    if status not in {"approved", "rejected"}:
        raise ValueError("Review status must be approved or rejected")
    return database.review_claim(claim_id, reviewer_id, status)
