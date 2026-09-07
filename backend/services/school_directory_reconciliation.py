"""Typed deterministic School Directory reconciliation planning.

This module never writes canonical data. Callers persist the generated manifest
and apply it only after validation succeeds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .school_directory_bootstrap import ManifestItem, deduplicate_manifest

TARGET_BASE_CLASSES = frozenset({"5", "6", "7-1", "7-2", "8", "9-А", "9-Д", "10", "11"})
FORBIDDEN_GROUP_KEYS = frozenset({"class:7", "math:5", "math:6", "math:7-1", "math:7-2", "math:8", "math:10", "exam:9:группа-математики"})
INACTIVE_SOURCE_NAMES = frozenset({"Стрижов Георгий", "Иващенко Фёдор", "Нестерова Алиса"})


@dataclass(frozen=True)
class CanonicalRelation:
    person_id: str
    relation_key: str
    source: str
    source_ref: str = ""
    manual_authoritative: bool = False


@dataclass
class ReconciliationManifest:
    items: list[ManifestItem] = field(default_factory=list)
    unresolved: list[ManifestItem] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items + self.unresolved:
            counts[item.action] = counts.get(item.action, 0) + 1
        return counts


def source_person_is_eligible(display_name: str, status: str | None) -> bool:
    """Stale roster rows cannot reactivate explicit lifecycle decisions."""
    return display_name not in INACTIVE_SOURCE_NAMES and status != "inactive"


def plan_relations(current: Iterable[CanonicalRelation], target: Iterable[CanonicalRelation], *, unresolved_keys: Iterable[tuple[str, str]] = ()) -> ReconciliationManifest:
    current_by_key: dict[tuple[str, str], list[CanonicalRelation]] = {}
    target_by_key: dict[tuple[str, str], CanonicalRelation] = {}
    for relation in current:
        current_by_key.setdefault((relation.person_id, relation.relation_key), []).append(relation)
    for relation in target:
        target_by_key[(relation.person_id, relation.relation_key)] = relation
    unresolved = set(unresolved_keys)
    manifest = ReconciliationManifest()
    for key, wanted in sorted(target_by_key.items()):
        existing = current_by_key.get(key, [])
        evidence = {"source_refs": [wanted.source_ref] if wanted.source_ref else [], "source": wanted.source}
        if key in unresolved:
            manifest.unresolved.append(ManifestItem("NEEDS_SOURCE", ":".join(key), {}, evidence))
        elif existing:
            manifest.items.append(ManifestItem("KEEP", ":".join(key), {}, evidence))
            if len(existing) > 1:
                manifest.items.append(ManifestItem("DEDUPLICATE", ":".join(key), {"rows": len(existing)}, {"source_refs": [item.source_ref for item in existing if item.source_ref]}))
        else:
            manifest.items.append(ManifestItem("ADD", ":".join(key), {}, evidence))
    for key, existing in sorted(current_by_key.items()):
        if key in target_by_key or key in unresolved:
            continue
        manual = [item for item in existing if item.manual_authoritative]
        item = ManifestItem("NEEDS_SOURCE" if manual else "REMOVE", ":".join(key), {"manual_authoritative": bool(manual)}, {"source_refs": [item.source_ref for item in existing if item.source_ref]})
        (manifest.unresolved if manual else manifest.items).append(item)
    manifest.items = deduplicate_manifest(manifest.items)
    manifest.unresolved = deduplicate_manifest(manifest.unresolved)
    return manifest


def validate_manifest(manifest: ReconciliationManifest, *, active_base_counts: Mapping[str, int], target_group_keys: Iterable[str], previous_counts: Mapping[str, int], target_counts: Mapping[str, int]) -> None:
    bad_bases = {person_id: count for person_id, count in active_base_counts.items() if count > 1}
    if bad_bases:
        raise ValueError(f"multiple active base cohorts: {sorted(bad_bases)}")
    group_keys = set(target_group_keys)
    forbidden = sorted(group_keys & FORBIDDEN_GROUP_KEYS)
    if forbidden:
        raise ValueError(f"forbidden target groups: {forbidden}")
    previous = int(previous_counts.get("memberships", 0))
    target = int(target_counts.get("memberships", 0))
    if previous >= 20 and previous - target >= max(10, previous // 3):
        raise ValueError("mass-removal anomaly: canonical apply quarantined")
    manifest.diagnostics.update({"validated": True, "active_base_people": len(active_base_counts), "target_groups": len(group_keys)})
