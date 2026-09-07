"""Safe, explainable identity reconciliation for School Directory sources.

The resolver is deliberately conservative: a short-name variant is evidence,
not proof.  Context (surname, class and source observations) must make the
candidate unique before a canonical relationship can be changed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping


SHORT_NAME_VARIANTS: dict[str, str] = {
    "вика": "виктория", "петя": "петр", "тася": "таисия", "тая": "таисия",
    "маша": "мария", "оля": "ольга", "оля": "ольга", "катя": "екатерина",
    "саша": "александра", "миша": "михаил", "леша": "алексей", "лёша": "алексей",
    "артём": "артем", "вася": "василий", "федя": "федор", "коля": "николай",
    "лена": "елена", "алёна": "алена", "алена": "алена",
}


def normalize_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip().casefold()
    return text.replace("ё", "е")


def name_parts(value: str) -> tuple[str, str]:
    parts = normalize_name(value).split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (normalize_name(value), "")


def variant_key(value: str) -> str:
    surname, given = name_parts(value)
    return f"{surname} {SHORT_NAME_VARIANTS.get(given, given)}".strip()


@dataclass(frozen=True)
class IdentityCandidate:
    identity_id: str
    display_name: str
    class_name: str | None = None
    memberships: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceObservation:
    source_ref: str
    source_spelling: str
    identity_id: str
    match_method: str
    evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolution:
    identity_id: str | None
    method: str
    confidence: float
    evidence: Mapping[str, object]
    candidates: tuple[str, ...] = ()
    reason: str = ""


def resolve_identity(
    source_name: str,
    candidates: Iterable[IdentityCandidate],
    *,
    class_name: str | None = None,
    source_ref: str = "",
    observations: Iterable[SourceObservation] = (),
) -> Resolution:
    """Resolve only when a deterministic stage produces one candidate."""
    normalized = normalize_name(source_name)
    candidates = tuple(candidates)
    exact = tuple(c for c in candidates if normalize_name(c.display_name) == normalized)
    if len(exact) == 1:
        return Resolution(exact[0].identity_id, "exact", 1.0, {"source_name": source_name})
    normalized_matches = tuple(c for c in candidates if normalize_name(c.display_name) == normalized)
    if len(normalized_matches) == 1:
        return Resolution(normalized_matches[0].identity_id, "normalization", .99, {"source_name": source_name})
    observed = tuple(o for o in observations if normalize_name(o.source_spelling) == normalized and (not source_ref or o.source_ref == source_ref))
    observed_ids = {o.identity_id for o in observed}
    if len(observed_ids) == 1:
        identity_id = next(iter(observed_ids))
        return Resolution(identity_id, "source_observation", .98, {"source_ref": source_ref})

    target_variant = variant_key(source_name)
    variant_matches = tuple(c for c in candidates if variant_key(c.display_name) == target_variant)
    if class_name:
        compatible = tuple(c for c in variant_matches if not c.class_name or c.class_name == class_name or (c.class_name == "9-А" and class_name == "9") or (c.class_name == "9-Д" and class_name == "9"))
        variant_matches = compatible
    if len(variant_matches) == 1:
        c = variant_matches[0]
        return Resolution(c.identity_id, "name_variant_context", .95, {"source_name": source_name, "class_name": class_name})
    if variant_matches:
        return Resolution(None, "unresolved", 0.0, {"source_name": source_name}, tuple(c.identity_id for c in variant_matches), "multiple candidates remain")
    return Resolution(None, "unresolved", 0.0, {"source_name": source_name}, (), "no deterministic candidate")


def validate_semantic_proposal(proposal: Mapping[str, object], resolution: Resolution) -> bool:
    """Groq may suggest; it cannot bypass deterministic gates."""
    proposed = str(proposal.get("identity_id") or "")
    if not proposed or proposed != resolution.identity_id:
        return False
    if resolution.method not in {"source_observation", "name_variant_context"}:
        return False
    try:
        confidence = float(proposal.get("confidence", 0))
    except (TypeError, ValueError):
        return False
    return confidence >= 0.85 and bool(proposal.get("evidence"))


def plan_disappeared_source_records(previous_refs: set[str], current_refs: set[str], *, source_authoritative: bool) -> tuple[str, ...]:
    """Return only source-derived refs that should be ended; never delete identities."""
    if not source_authoritative:
        return ()
    return tuple(sorted(previous_refs - current_refs))


def bulk_merge_is_safe(merge_count: int, active_identity_count: int, *, max_merges: int = 10) -> bool:
    if merge_count <= 0:
        return True
    return merge_count <= max_merges and merge_count <= max(1, active_identity_count // 5)
