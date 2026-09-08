"""Read-only student membership reconciliation.

The module deliberately has no write path.  It combines the current Google
rosters with a production snapshot and returns an inspectable plan.  Applying
the plan is intentionally out of scope for this pass.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from backend.config import Settings
from backend.database import Database
from backend.services.google_live import GoogleLiveClient, GoogleLiveError, GoogleTokenStore
from backend.services.google_oauth import google_config
from backend.services.student_membership_manual_resolutions import (
    alias_for_source,
    display_name_override,
    group_assignment_for_source,
    load_manual_resolutions,
)
from backend.services.identity_reconciliation import (
    IdentityCandidate,
    SourceObservation,
    normalize_name,
    resolve_identity,
)
from backend.services.school_directory_bootstrap import (
    DirectoryGroup,
    DirectoryMembership,
    DirectorySelection,
    name_key,
    parse_class_lists,
    parse_exam_selections,
    parse_group_rosters,
)


CLASS_TAB = "Списки по классам 26/27"
GROUP_TAB = "списки групп 26-27"
EXAM_TAB = "ОГЭ/ЕГЭ"
RULE_TAB = "Структура школы — правила"
REPORT_DATE = "2026-09-08"


@dataclass(frozen=True)
class ExpectedRelation:
    identity_id: str
    group_id: str
    identity_name: str
    group_name: str
    source_refs: tuple[str, ...]
    authority: str
    rule: str
    resolution: str = "deterministic"

    def key(self) -> tuple[str, str]:
        return self.identity_id, self.group_id


@dataclass(frozen=True)
class Issue:
    kind: str
    reason: str
    details: Mapping[str, Any]


@dataclass
class ProductionSnapshot:
    identities: list[dict[str, Any]]
    groups: list[dict[str, Any]]
    memberships: list[dict[str, Any]]
    mappings: list[dict[str, Any]]
    selections: list[dict[str, Any]]
    source_snapshots: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DryRunPlan:
    source_tabs: dict[str, Any]
    expected: list[ExpectedRelation] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    assignments: list[dict[str, Any]] = field(default_factory=list)
    selections: list[dict[str, Any]] = field(default_factory=list)
    invariants: list[dict[str, Any]] = field(default_factory=list)
    computed_audiences_skipped: list[dict[str, Any]] = field(default_factory=list)
    human_confirmation_required: list[dict[str, Any]] = field(default_factory=list)
    known_edge_cases: list[dict[str, Any]] = field(default_factory=list)
    literature_base_recheck: dict[str, Any] = field(default_factory=dict)
    production_writes_performed: int = 0
    source_rows: dict[str, int] = field(default_factory=dict)
    canonical_students_inspected: int = 0
    proposed_group_preparations: list[dict[str, Any]] = field(default_factory=list)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json(item) for item in value]
    return str(value)


def _same(value: Any, *options: str) -> bool:
    normalized = normalize_name(_s(value))
    return normalized in {normalize_name(option) for option in options}


def _grade(value: Any) -> str:
    text = _s(value).strip()
    match = re.match(r"^(\d+)", text)
    return match.group(1) if match else text


def _subject_key(value: Any) -> str:
    text = normalize_name(_s(value)).replace(" профиль", "")
    return "обществознание" if text in {"общество", "обществознание"} else text


def _semantic_subgroup(group: DirectoryGroup) -> tuple[str | None, str | None]:
    label = _s(group.subject_subgroup)
    track = group.exam_track
    if track:
        return track.casefold(), track
    first = label.split(" ", 1)[0].strip(".").casefold()
    if first in {"база", "base"}:
        return "base", None
    if first in {"угл", "углубленный", "advanced"}:
        return "advanced", None
    return first or None, None


def _group_candidates(source: DirectoryGroup, groups: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if source.group_type == "class":
        return [g for g in groups if g["group_type"] == "class" and _s(g["name"]) == source.name.removeprefix("class:")]
    if source.name.startswith("english:"):
        return [g for g in groups if _s(g["name"]) == source.name]
    source_subject = normalize_name(source.subject or "")
    source_grade = _grade(source.base_class_name)
    source_semantic, source_track = _semantic_subgroup(source)
    candidates = []
    for group in groups:
        if group.get("group_type") != "subject_group":
            continue
        if normalize_name(group.get("subject")) != source_subject:
            continue
        if source_grade and _grade(group.get("base_class_name")) != source_grade:
            continue
        candidate_track = _s(group.get("exam_track"))
        candidate_subgroup = _s(group.get("subject_subgroup"))
        candidate_semantic = "advanced" if normalize_name(candidate_subgroup) in {"угл", "углубленный", "advanced"} else (
            "base" if normalize_name(candidate_subgroup) in {"база", "base"} else normalize_name(candidate_subgroup)
        )
        if source_track and normalize_name(candidate_track) != normalize_name(source_track):
            continue
        if source_semantic and candidate_semantic != source_semantic:
            continue
        candidates.append(group)
    return candidates


def _is_stable_roster(source: DirectoryGroup) -> bool:
    """Filter instructional source columns that are only ordinary subject splits."""
    if source.name.startswith("english:"):
        return True
    if source.subject == "Математика":
        if _grade(source.base_class_name) == "9":
            return source.subject_subgroup in {"A", "B", "C"}
        if _grade(source.base_class_name) == "11":
            return source.subject_subgroup.split(" ", 1)[0].strip(".").casefold() in {"база", "угл", "углубленный"}
        # Ordinary 5/6/7/8 math routes through the base class; Grade 10 has
        # no separate Math EGE 10 canonical group in the current model.
        return False
    return True


def load_production_snapshot(database: Database) -> ProductionSnapshot:
    """Read current production state with SELECTs only."""
    with database.connection() as connection:
        def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return [dict(row) for row in database.execute(connection, query, params).fetchall()]

        identities = rows("SELECT id, display_name, class_name, status, origin, source_ref FROM identities WHERE kind = 'student' ORDER BY display_name")
        groups = rows("SELECT id, name, display_name, group_type, subject, base_class_name, subject_subgroup, exam_track, provenance_source, provenance_ref, canonical FROM groups ORDER BY group_type, name")
        memberships = rows(
            """SELECT m.id, m.identity_id, i.display_name, i.class_name, i.status AS identity_status,
                      m.group_id, g.name AS group_name, g.group_type, g.subject, g.base_class_name,
                      g.subject_subgroup, g.exam_track, m.source, m.source_ref, m.active,
                      m.valid_from, m.valid_until
                 FROM memberships m JOIN identities i ON i.id = m.identity_id
                 JOIN groups g ON g.id = m.group_id
                WHERE m.member_role = 'student' AND m.active IS TRUE
                ORDER BY i.display_name, g.name, m.id"""
        )
        mappings = rows(
            """SELECT sm.id, sm.external_key, sm.mapping_type, sm.identity_id, sm.group_id,
                      sm.canonical_value, sm.status, sm.manually_confirmed, sm.evidence,
                      ss.display_name AS source_name, ss.external_key AS source_external_key
                 FROM school_source_mappings sm JOIN school_sources ss ON ss.id = sm.source_id
                WHERE sm.valid_until IS NULL AND sm.status <> 'revoked'
                ORDER BY sm.mapping_type, sm.id"""
        )
        selections = rows(
            """SELECT sf.id, sf.identity_id, i.display_name, sf.academic_year, sf.grade_level,
                      sf.subject, sf.selection_kind, sf.source, sf.source_ref, sf.evidence
                 FROM student_selection_facts sf JOIN identities i ON i.id = sf.identity_id
                WHERE sf.active IS TRUE
                ORDER BY sf.grade_level, i.display_name, sf.subject"""
        )
        source_snapshots = rows(
            """SELECT ss.id, src.display_name, src.external_key, src.authority_status,
                      ss.status, ss.is_last_known_valid, ss.observed_at, ss.created_at,
                      COUNT(sr.id) AS record_count
                 FROM school_source_snapshots ss JOIN school_sources src ON src.id = ss.source_id
                 LEFT JOIN school_source_records sr ON sr.snapshot_id = ss.id
                GROUP BY ss.id, src.display_name, src.external_key, src.authority_status,
                         ss.status, ss.is_last_known_valid, ss.observed_at, ss.created_at
                ORDER BY ss.created_at DESC"""
        )
    return ProductionSnapshot(identities, groups, memberships, mappings, selections, source_snapshots)


def _observations(snapshot: ProductionSnapshot) -> tuple[SourceObservation, ...]:
    result: list[SourceObservation] = []
    for mapping in snapshot.mappings:
        if mapping.get("mapping_type") != "identity" or mapping.get("status") != "confirmed" or not mapping.get("identity_id"):
            continue
        evidence = mapping.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except json.JSONDecodeError:
                evidence = {}
        spelling = evidence.get("source_spelling") or evidence.get("source_name")
        source_ref = evidence.get("source_ref") or ""
        if spelling:
            result.append(SourceObservation(str(source_ref), str(spelling), str(mapping["identity_id"]), str(evidence.get("match_method") or "confirmed_mapping"), evidence))
    return tuple(result)


def _resolve_person(name: str, class_name: str | None, source_ref: str, snapshot: ProductionSnapshot, manual_resolutions: Mapping[str, Any] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    manual_resolutions = manual_resolutions or {}
    alias = alias_for_source(dict(manual_resolutions), name, source_ref)
    target_name = str(alias.get("target_display_name")) if alias else name
    if display_name_override(dict(manual_resolutions), target_name) and display_name_override(dict(manual_resolutions), target_name).get("mode") == "excluded_current_structure":
        return None, {"source_name": name, "source_ref": source_ref, "reason": "manual exclusion from current structure", "manual_excluded": True, "manual_confirmation": "confirmed_by_human"}
    # Merged/inactive rows are never resolution targets.  Existing confirmed
    # aliases and the deterministic variant contract can resolve them to the
    # active canonical identity without creating or merging anything.
    candidates = [IdentityCandidate(str(item["id"]), str(item["display_name"]), item.get("class_name")) for item in snapshot.identities if item.get("status") == "active"]
    resolution_class = None if _grade(class_name) == "7" else class_name
    resolution = resolve_identity(target_name, candidates, class_name=resolution_class, source_ref=source_ref, observations=_observations(snapshot))
    if not resolution.identity_id:
        pending = next((item for item in manual_resolutions.get("identity_pending", []) if normalize_name(item.get("source_name")) == normalize_name(name)), None)
        return None, {"source_name": name, "source_ref": source_ref, "class_name": class_name, "method": resolution.method, "reason": "manual identity pending; no identity is created in read-only pass" if pending else resolution.reason, "candidates": list(resolution.candidates), "manual_identity_pending": bool(pending), "manual_confirmation": "confirmed_by_human" if pending else None}
    identity = next((item for item in snapshot.identities if str(item["id"]) == resolution.identity_id), None)
    if not identity:
        return None, {"source_name": name, "source_ref": source_ref, "reason": "resolved identity is absent from snapshot", "manual_identity_pending": bool(alias), "manual_confirmation": "confirmed_by_human" if alias else None}
    return identity, None


def _rule_for_group(group: dict[str, Any]) -> str:
    name = _s(group.get("name"))
    if group.get("group_type") == "class":
        return "authoritative base class roster"
    if name.startswith("english:"):
        return "one real English group per source roster; Grade 9 OGE is replacement, not overlay"
    if name in {"grade9-math-A", "grade9-math-B", "grade9-math-C"}:
        return "Grade 9 Math A/B/C exactly one"
    subject = _s(group.get("subject"))
    grade = _grade(group.get("base_class_name"))
    if grade == "11" and subject in {"Математика", "Обществознание", "Литература"}:
        return f"Grade 11 {subject}: base XOR EGE/advanced"
    return "authoritative instructional roster; schedule metadata does not create membership"


def _add_expected(expected: dict[tuple[str, str], ExpectedRelation], identity: dict[str, Any], group: dict[str, Any], refs: Iterable[str], authority: str, rule: str) -> None:
    relation = ExpectedRelation(str(identity["id"]), str(group["id"]), str(identity["display_name"]), str(group["name"]), tuple(sorted(set(refs))), authority, rule)
    previous = expected.get(relation.key())
    if previous:
        expected[relation.key()] = ExpectedRelation(previous.identity_id, previous.group_id, previous.identity_name, previous.group_name, tuple(sorted(set(previous.source_refs + relation.source_refs))), previous.authority, previous.rule)
    else:
        expected[relation.key()] = relation


def build_expected_memberships(source_rows: Mapping[str, Sequence[Sequence[Any]]], snapshot: ProductionSnapshot, manual_resolutions: Mapping[str, Any] | None = None) -> tuple[list[ExpectedRelation], list[Issue], dict[str, Any]]:
    manual_resolutions = manual_resolutions or {}
    class_people, class_groups, class_members = parse_class_lists(source_rows[CLASS_TAB])
    roster_groups, roster_members, parser_issues = parse_group_rosters(source_rows[GROUP_TAB])
    expected: dict[tuple[str, str], ExpectedRelation] = {}
    issues: list[Issue] = []
    canonical_groups = [g for g in snapshot.groups if g.get("canonical")]
    group_by_source: dict[str, dict[str, Any]] = {}
    for source_group in [*class_groups, *roster_groups]:
        if source_group.group_type != "class" and not _is_stable_roster(source_group):
            continue
        candidates = _group_candidates(source_group, canonical_groups)
        if len(candidates) == 1:
            group_by_source[source_group.name] = candidates[0]
        elif source_group.group_type == "class" and source_group.name == "class:7":
            continue
        elif next((item for item in manual_resolutions.get("group_preparations", []) if normalize_name(item.get("subject")) == normalize_name(source_group.subject) and _grade(item.get("base_class_name")) == _grade(source_group.base_class_name) and normalize_name(item.get("exam_track")) == normalize_name(source_group.exam_track)), None):
            preparation = next(item for item in manual_resolutions["group_preparations"] if normalize_name(item.get("subject")) == normalize_name(source_group.subject) and _grade(item.get("base_class_name")) == _grade(source_group.base_class_name) and normalize_name(item.get("exam_track")) == normalize_name(source_group.exam_track))
            group_by_source[source_group.name] = preparation | {"id": f"future:{preparation['name']}", "canonical": False}
        elif source_group.group_type == "class" or any(m.group == source_group.name for m in roster_members):
            issue_kind = "source_contradiction" if source_group.subject == "История" and _grade(source_group.base_class_name) == "9" and source_group.exam_track == "ОГЭ" else "ambiguous_group"
            issues.append(Issue(issue_kind, "source group has no unique canonical mapping", {"raw_source_group": source_group.name, "display_name": source_group.display_name, "source_ref": source_group.source_ref, "candidate_groups": [g.get("name") for g in candidates], "reason": "current roster has evidence, but no confirmed canonical instructional group exists; no group is created", "structural_rule": "Grade 9 History OGE instructional group is not confirmed without safe canonical target" if issue_kind == "source_contradiction" else "group mapping is not deterministic; no group is created"}))
    class_groups_by_name = {str(group.get("name")): group for group in canonical_groups if group.get("group_type") == "class"}
    derived_grade7: dict[str, dict[str, Any]] = {}
    for source_group in roster_groups:
        if source_group.subject != "Математика" or _grade(source_group.base_class_name) != "7":
            continue
        label = source_group.subject_subgroup.split(" ", 1)[0].strip()
        target_class = label if label in {"7-1", "7-2"} else None
        target_group = class_groups_by_name.get(target_class or "")
        if not target_group:
            continue
        for source_membership in [item for item in roster_members if item.group == source_group.name]:
            identity, issue = _resolve_person(source_membership.person, "7", source_membership.source_ref, snapshot, manual_resolutions)
            if issue:
                if not issue.get("manual_excluded"):
                    issues.append(Issue("manual_pending" if issue.get("manual_identity_pending") else "ambiguous_identity", "Grade 7 split roster person unresolved", {**issue, "raw_source_group": source_group.name, "authoritative_source": GROUP_TAB}))
                continue
            derived_grade7[str(identity["id"])] = target_group
            _add_expected(expected, identity, target_group, [source_membership.source_ref], GROUP_TAB, "Grade 7 base class is derived from the 7-1/7-2 stable split")
    class_name_by_person: dict[str, str] = {}
    for membership in class_members:
        source_group = next((g for g in class_groups if g.name == membership.group), None)
        if not source_group:
            continue
        identity, issue = _resolve_person(membership.person, source_group.base_class_name, membership.source_ref, snapshot, manual_resolutions)
        if issue:
            if not issue.get("manual_excluded"):
                issues.append(Issue("manual_pending" if issue.get("manual_identity_pending") else "ambiguous_identity", "base roster person unresolved", {**issue, "authoritative_source": CLASS_TAB}))
            continue
        group = group_by_source.get(membership.group)
        if source_group.name == "class:7":
            group = derived_grade7.get(str(identity["id"]))
            if not group:
                assignment = group_assignment_for_source(dict(manual_resolutions), membership.person, membership.source_ref)
                if assignment:
                    manual_group = class_groups_by_name.get(str(assignment.get("target_group_name")))
                    if manual_group:
                        group = manual_group
                if not group:
                    issues.append(Issue("source_contradiction", "Grade 7 class roster member has no 7-1/7-2 split", {"student": identity.get("display_name"), "source_ref": membership.source_ref, "rule": "Grade 7 base classes are 7-1 and 7-2"}))
        if not group:
            continue
        _add_expected(expected, identity, group, [membership.source_ref], CLASS_TAB, "authoritative base class roster")
        class_name_by_person[str(identity["id"])] = str(group["name"])

    # Rule-derived stable groups whose individual roster is fixed by class scope.
    for identity_id, class_name in class_name_by_person.items():
        identity = next(item for item in snapshot.identities if str(item["id"]) == identity_id)
        grade = _grade(class_name)
        if grade in {"5", "6", "7", "8"}:
            course = next((g for g in canonical_groups if g.get("name") == "generic:course-choice"), None)
            if course:
                _add_expected(expected, identity, course, ["Структура школы — правила!A2:F33", "target-rule:grades-5-8"], RULE_TAB, "Grade 5-8 stable course-choice rule")
        if grade in {"7", "8"}:
            digital = next((g for g in canonical_groups if g.get("name") == "generic:digital-track"), None)
            if digital:
                _add_expected(expected, identity, digital, ["Структура школы — правила!A4:F33", "target-rule:grades-7-8"], RULE_TAB, "Grade 7-8 stable digital-track rule")

    for membership in roster_members:
        source_group = next((g for g in roster_groups if g.name == membership.group), None)
        if not source_group or not _is_stable_roster(source_group):
            continue
        group = group_by_source.get(membership.group)
        if not group:
            continue
        identity, issue = _resolve_person(membership.person, source_group.base_class_name, membership.source_ref, snapshot, manual_resolutions)
        if issue:
            if not issue.get("manual_excluded"):
                issues.append(Issue("manual_pending" if issue.get("manual_identity_pending") else "ambiguous_identity", "instructional roster person unresolved", {**issue, "raw_source_group": membership.group, "authoritative_source": GROUP_TAB}))
            continue
        _add_expected(expected, identity, group, [membership.source_ref], GROUP_TAB, _rule_for_group(group))

    # Existing Literature 11 Base is a derived canonical group, not a new
    # source column: class 11 minus the current Literature EGE roster.
    class11 = next((g for g in canonical_groups if g.get("group_type") == "class" and g.get("name") == "11"), None)
    lit_base = next((g for g in canonical_groups if g.get("name") == "instructional:литература:11:база"), None)
    lit_ege = next((g for g in canonical_groups if g.get("name") == "instructional:литература:11:егэ"), None)
    if class11 and lit_base and lit_ege:
        class11_ids = {str(item.identity_id) for item in expected.values() if item.group_id == str(class11["id"])}
        lit_ege_ids = {str(item.identity_id) for item in expected.values() if item.group_id == str(lit_ege["id"])}
        if lit_ege_ids.issubset(class11_ids):
            for identity_id in sorted(class11_ids - lit_ege_ids):
                identity = next(item for item in snapshot.identities if str(item["id"]) == identity_id)
                _add_expected(expected, identity, lit_base, ["списки групп 26-27!R50", "derived=11-minus-literature-ege"], "Структура школы — правила", "Grade 11 Literature Base = class 11 minus Literature EGE")
        else:
            issues.append(Issue("source_contradiction", "Literature EGE is not a subset of Grade 11 class", {"class11_ids": sorted(class11_ids), "literature_ege_ids": sorted(lit_ege_ids), "rule": "Grade 11 Literature Base = class 11 minus Literature EGE"}))

    selections, selection_issues = parse_exam_selections(source_rows[EXAM_TAB])
    for parser_issue in [*parser_issues, *selection_issues]:
        issues.append(Issue("source_parser_issue", parser_issue.issue_type, {"natural_key": parser_issue.natural_key, "details": parser_issue.details, "source_refs": list(parser_issue.source_refs)}))
    diagnostics = {
        "class_people": len(class_people),
        "class_memberships": len(class_members),
        "roster_groups": len(roster_groups),
        "roster_memberships": len(roster_members),
        "selection_facts": len(selections),
        "instructional_source_groups_skipped": [g.display_name for g in roster_groups if not _is_stable_roster(g)],
        "source_selections": selections,
    }
    return list(expected.values()), issues, diagnostics


def _current_by_key(memberships: Sequence[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in memberships:
        result[(str(row["identity_id"]), str(row["group_id"]))].append(row)
    return result


def _wrong_group(current: dict[str, Any], expected: Sequence[ExpectedRelation], current_rows: Mapping[tuple[str, str], list[dict[str, Any]]]) -> bool:
    identity_id = str(current["identity_id"])
    current_name = str(current["group_name"])
    target_names = {item.group_name for item in expected if item.identity_id == identity_id}
    if current_name.startswith("english:"):
        return any(name.startswith("english:") and name != current_name for name in target_names)
    if current_name in {"grade9-math-A", "grade9-math-B", "grade9-math-C"}:
        return any(name in {"grade9-math-A", "grade9-math-B", "grade9-math-C"} and name != current_name for name in target_names)
    return False


def _stale_action(row: dict[str, Any], expected: Sequence[ExpectedRelation], current_rows: Mapping[tuple[str, str], list[dict[str, Any]]], manual_resolutions: Mapping[str, Any] | None = None) -> tuple[str, str]:
    manual_resolutions = manual_resolutions or {}
    override = display_name_override(dict(manual_resolutions), str(row.get("display_name") or ""))
    if override and override.get("mode") == "excluded_current_structure":
        return "DEACTIVATE", "manual confirmation excludes this identity from current structure; close validity/history, never physical delete"
    if row.get("identity_status") != "active":
        return "PROTECTED", "canonical identity is inactive/merged; membership must not be changed by this pass"
    if row.get("source") == "admin_override" or str(row.get("source_ref") or "").startswith("manual"):
        return "PROTECTED", "manual/admin provenance requires explicit human decision"
    if _wrong_group(row, expected, current_rows):
        return "DEACTIVATE", "wrong-group relation is contradicted by the authoritative one-of partition"
    source_ref = _s(row.get("source_ref"))
    if row.get("source") == "official_import" and (source_ref.startswith(CLASS_TAB) or source_ref.startswith(GROUP_TAB) or "derived=11-minus-literature-ege" in source_ref or source_ref.startswith("target-rule:")):
        return "DEACTIVATE", "current authoritative roster/rule no longer contains this exact relation"
    return "PROTECTED", "source disappearance or weak provenance is insufficient for safe deactivation"


def reconcile_memberships(expected: Sequence[ExpectedRelation], snapshot: ProductionSnapshot, issues: Sequence[Issue], source_rows: Mapping[str, Sequence[Sequence[Any]]], manual_resolutions: Mapping[str, Any] | None = None) -> DryRunPlan:
    manual_resolutions = manual_resolutions or {}
    plan = DryRunPlan(source_tabs={"spreadsheet_title": "Расписание  2026/27", "tabs": [CLASS_TAB, GROUP_TAB, EXAM_TAB, RULE_TAB]}, expected=list(expected), issues=list(issues), source_rows={key: len(value) for key, value in source_rows.items()}, canonical_students_inspected=len(snapshot.identities))
    current = _current_by_key(snapshot.memberships)
    expected_by_key = {item.key(): item for item in expected}
    for key, item in sorted(expected_by_key.items(), key=lambda pair: (pair[1].identity_name, pair[1].group_name)):
        rows = current.get(key, [])
        if not rows:
            plan.assignments.append({"action": "CREATE", "identity_id": item.identity_id, "student": item.identity_name, "group_id": item.group_id, "group": item.group_name, "source_refs": list(item.source_refs), "authoritative_source": item.authority, "structural_rule": item.rule, "confidence": "deterministic"})
        else:
            plan.assignments.append({"action": "KEEP" if len(rows) == 1 else "DUPLICATE", "identity_id": item.identity_id, "student": item.identity_name, "group_id": item.group_id, "group": item.group_name, "membership_ids": [str(row["id"]) for row in rows], "source_refs": list(item.source_refs), "authoritative_source": item.authority, "structural_rule": item.rule, "confidence": "deterministic"})
            if len(rows) > 1:
                for duplicate in rows[1:]:
                    plan.assignments.append({"action": "DEACTIVATE", "classification": "DUPLICATE", "identity_id": item.identity_id, "student": item.identity_name, "group": item.group_name, "membership_id": str(duplicate["id"]), "current_source": duplicate.get("source"), "current_source_ref": duplicate.get("source_ref"), "stale_reason": "duplicate active logical membership", "authoritative_source": item.authority, "structural_rule": item.rule, "why_safe": "same identity/group has another active row; preserve one survivor"})
    for key, rows in sorted(current.items(), key=lambda pair: (_s(pair[1][0].get("display_name")), _s(pair[1][0].get("group_name")))):
        if key in expected_by_key:
            continue
        for row in rows:
            action, reason = _stale_action(row, expected, current, manual_resolutions)
            item = {"action": action, "classification": "WRONG_GROUP" if _wrong_group(row, expected, current) else ("STALE" if action == "DEACTIVATE" else "PROTECTED"), "identity_id": str(row["identity_id"]), "student": row.get("display_name"), "group_id": str(row["group_id"]), "group": row.get("group_name"), "membership_id": str(row["id"]), "current_source": row.get("source"), "current_source_ref": row.get("source_ref"), "stale_reason": reason, "authoritative_source": GROUP_TAB if row.get("group_type") != "class" else CLASS_TAB, "structural_rule": _rule_for_group(row), "confidence": "high" if action == "DEACTIVATE" else "manual/protected", "why_safe": "exact source-backed relation is absent and provenance is current authoritative import" if action == "DEACTIVATE" else "absence is not sufficient to remove this relation safely"}
            plan.assignments.append(item)

    # Selection-vs-roster report.  Only source roster evidence creates an
    # instructional relation; a selection by itself never creates one.
    selections, _ = parse_exam_selections(source_rows[EXAM_TAB])
    selection_by_person: dict[str, list[DirectorySelection]] = defaultdict(list)
    for selection in selections:
        identity, issue = _resolve_person(selection.person, selection.grade, selection.source_ref, snapshot, manual_resolutions)
        if issue:
            if issue.get("manual_excluded"):
                continue
            plan.issues.append(Issue("ambiguous_identity", "exam selection person unresolved", {**issue, "authoritative_source": EXAM_TAB}))
            continue
        selection_by_person[str(identity["id"])].append(selection)
    for identity_id, items in selection_by_person.items():
        identity = next(item for item in snapshot.identities if str(item["id"]) == identity_id)
        for selection in items:
            roster = [item for item in expected if item.identity_id == identity_id and item.group_name.startswith("instructional:") and _grade(next((g.get("base_class_name") for g in snapshot.groups if str(g.get("id")) == item.group_id), "")) == _grade(selection.grade) and _subject_key(next((g.get("subject") for g in snapshot.groups if str(g.get("id")) == item.group_id), "")) == _subject_key(selection.subject)]
            plan.selections.append({"student": identity.get("display_name"), "identity_id": identity_id, "grade": selection.grade, "subject": selection.subject, "selection_kind": selection.selection_kind, "selection_source_ref": selection.source_ref, "instructional_roster": bool(roster), "roster_groups": [item.group_name for item in roster], "classification": "SELECTION_AND_ROSTER" if roster else "SELECTION_ONLY_INTENTIONAL", "rule": "selection fact does not imply instructional membership"})
    expected_selection_keys = {(str(row["identity_id"]), _grade(row["grade_level"]), normalize_name(row["subject"])) for row in snapshot.selections}
    for item in plan.selections:
        key = (str(item["identity_id"]), _grade(item["grade"]), normalize_name(item["subject"]))
        if key not in expected_selection_keys:
            item["production_selection_fact"] = "MISSING"
        else:
            item["production_selection_fact"] = "PRESENT"
    # The inverse is intentionally informational: an instructional roster is
    # authoritative evidence of teaching, but it does not manufacture a
    # selection fact and does not get removed for lacking one.
    source_selection_keys = {(_s(item.get("identity_id")), _grade(item.get("grade")), _subject_key(item.get("subject"))) for item in plan.selections}
    for relation in expected:
        group = next((item for item in snapshot.groups if str(item.get("id")) == relation.group_id), None)
        if not group or not (group.get("exam_track") or relation.group_name in {"math:11:advanced", "instructional:обществознание:11:угл"}):
            continue
        grade = _grade(group.get("base_class_name"))
        subject = _subject_key(group.get("subject"))
        if (relation.identity_id, grade, subject) not in source_selection_keys:
            plan.selections.append({"student": relation.identity_name, "identity_id": relation.identity_id, "grade": grade, "subject": group.get("subject"), "selection_kind": "unknown", "selection_source_ref": None, "instructional_roster": True, "roster_groups": [relation.group_name], "classification": "ROSTER_WITHOUT_SELECTION", "rule": "instructional roster is separate from exam selection; do not auto-correct"})
    plan.computed_audiences_skipped = [{"rule": "Grade 11 students without Society EGE join Geography 10 lesson", "reason": "computed schedule audience, not permanent membership"}, {"rule": "Остальные = base slot minus routed parallel audiences", "reason": "computed audience, not canonical group"}, {"rule": "shared Physics/History 10-11 lessons", "reason": "shared lesson does not merge canonical groups"}]
    existing_group_names = {str(item.get("name")) for item in snapshot.groups}
    plan.proposed_group_preparations = [dict(item, production_action="CREATE_GROUP_IN_FUTURE_APPLY_ONLY") for item in manual_resolutions.get("group_preparations", []) if str(item.get("name")) not in existing_group_names]
    plan.invariants = evaluate_invariants(expected, snapshot, manual_resolutions)
    plan.human_confirmation_required = build_human_questions(plan)
    expected_by_identity: dict[str, list[str]] = defaultdict(list)
    for item in expected:
        expected_by_identity[item.identity_id].append(item.group_name)
    for name in ("Иващенко Фёдор", "Нестерова Алиса", "Холодова Татьяна", "Куренков Иван"):
        identities = [item for item in snapshot.identities if normalize_name(item.get("display_name")) == normalize_name(name)]
        current_rows = [row for row in snapshot.memberships if normalize_name(row.get("display_name")) == normalize_name(name)]
        issues_for_name = [issue.details for issue in plan.issues if normalize_name(issue.details.get("source_name") or issue.details.get("identity_name") or issue.details.get("student") or "") == normalize_name(name)]
        expected_names = sorted({group for item in identities for group in expected_by_identity.get(str(item["id"]), [])})
        partition_gap = []
        if any(_grade(item.get("class_name")) == "9" and item.get("status") == "active" for item in identities):
            if not any(item.startswith("grade9-math-") for item in expected_names):
                partition_gap.append("Grade 9 Math A/B/C")
            if not any(item.startswith("english:") for item in expected_names):
                partition_gap.append("Grade 9 English")
        if partition_gap:
            issues_for_name.append({"reason": "authoritative source does not provide required Grade 9 partition", "missing_partitions": partition_gap})
        override = display_name_override(dict(manual_resolutions), name)
        result = "excluded_current_structure" if override and override.get("mode") == "excluded_current_structure" else ("UNRESOLVED" if issues_for_name else "deterministically reconciled")
        plan.known_edge_cases.append({"student": name, "canonical_identities": [{"id": str(item["id"]), "display_name": item["display_name"], "status": item.get("status"), "class_name": item.get("class_name")} for item in identities], "current_active_groups": sorted({str(row.get("group_name")) for row in current_rows}), "expected_groups": expected_names, "issues": issues_for_name, "result": result})
    lit_base = next((item for item in snapshot.groups if item.get("name") == "instructional:литература:11:база"), None)
    if lit_base:
        current_lit = [row for row in snapshot.memberships if str(row.get("group_id")) == str(lit_base["id"])]
        expected_lit = [item for item in expected if item.group_id == str(lit_base["id"])]
        plan.literature_base_recheck = {"group_id": str(lit_base["id"]), "current_membership_count": len(current_lit), "expected_membership_count": len(expected_lit), "current_students": sorted(row.get("display_name") for row in current_lit), "expected_students": sorted(item.identity_name for item in expected_lit), "current_only": sorted(set(row.get("display_name") for row in current_lit) - set(item.identity_name for item in expected_lit)), "expected_only": sorted(set(item.identity_name for item in expected_lit) - set(row.get("display_name") for row in current_lit))}
    return plan


def evaluate_invariants(expected: Sequence[ExpectedRelation], snapshot: ProductionSnapshot, manual_resolutions: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    manual_resolutions = manual_resolutions or {}
    known_exception_names = {normalize_name(item.get("display_name")) for item in manual_resolutions.get("known_exceptions", []) if item.get("exception") == "grade9_math_english_partition"}
    checks: list[dict[str, Any]] = []
    expected_groups: dict[str, set[str]] = defaultdict(set)
    for relation in expected:
        expected_groups[relation.identity_id].add(relation.group_name)
    current_groups: dict[str, set[str]] = defaultdict(set)
    duplicate_counts = Counter()
    for row in snapshot.memberships:
        current_groups[str(row["identity_id"])].add(str(row["group_name"]))
        duplicate_counts[(str(row["identity_id"]), str(row["group_id"]))] += 1
    checks.append({"name": "global_base_class_at_most_one", "status": "PASS" if all(sum(1 for name in names if name in {"5", "6", "7-1", "7-2", "8", "9-А", "9-Д", "10", "11"}) <= 1 for names in current_groups.values()) else "FAIL", "details": "current active memberships"})
    checks.append({"name": "global_no_duplicate_logical_membership", "status": "PASS" if max(duplicate_counts.values(), default=0) <= 1 else "FAIL", "details": [list(key) + [count] for key, count in duplicate_counts.items() if count > 1]})
    for label, group_names in (("grade9_math_exactly_one", {"grade9-math-A", "grade9-math-B", "grade9-math-C"}), ("grade9_english_exactly_one", {f"english:{i}" for i in range(6, 9)})):
        rows = []
        for identity in snapshot.identities:
            if identity.get("status") != "active" or _grade(identity.get("class_name")) != "9":
                continue
            names = expected_groups.get(str(identity["id"]), set())
            rows.append({"student": identity["display_name"], "count": len(names & group_names), "groups": sorted(names & group_names)})
        failures = [row for row in rows if row["count"] != 1]
        unexpected = [row for row in failures if normalize_name(row["student"]) not in known_exception_names]
        known = [row for row in failures if normalize_name(row["student"]) in known_exception_names]
        checks.append({"name": label, "status": "FAIL" if unexpected or not rows else ("PASS_WITH_KNOWN_EXCEPTION" if known else "PASS"), "details": unexpected, "known_exceptions": known})
    for label, names in (("grade11_math_xor", {"math:11:base", "math:11:advanced"}), ("grade11_society_xor", {"instructional:обществознание:11:база", "instructional:обществознание:11:угл"}), ("grade11_literature_xor", {"instructional:литература:11:база", "instructional:литература:11:егэ"})):
        rows = []
        for identity in snapshot.identities:
            if identity.get("status") != "active" or _grade(identity.get("class_name")) != "11":
                continue
            names_for_student = expected_groups.get(str(identity["id"]), set()) & names
            rows.append({"student": identity["display_name"], "count": len(names_for_student), "groups": sorted(names_for_student)})
        checks.append({"name": label, "status": "PASS" if rows and all(row["count"] == 1 for row in rows) else "FAIL", "details": [row for row in rows if row["count"] != 1]})
    forbidden = {"9-1", "9-2", "9-3", "class:7", "instructional:литература:10:егэ", "instructional:химия:11:егэ", "instructional:биология:11:егэ"}
    active_forbidden = sorted({str(row["group_name"]) for row in snapshot.memberships if str(row["group_name"]) in forbidden})
    checks.append({"name": "forbidden_active_canonical_memberships_absent", "status": "PASS" if not active_forbidden else "FAIL", "details": active_forbidden})
    return checks


def build_human_questions(plan: DryRunPlan) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for issue in plan.issues:
        if issue.kind not in {"ambiguous_identity", "ambiguous_group", "source_contradiction"}:
            continue
        details = dict(issue.details)
        questions.append({"question": issue.reason, "student_or_group": details.get("identity_name") or details.get("source_name") or details.get("student") or details.get("raw_source_group") or details.get("natural_key"), "sources": details, "production": "no mutation performed", "options": ["confirm the deterministic mapping/rule and include in a later apply-pass", "correct the source/mapping first and rerun dry-run"], "why_code_cannot_choose": "authoritative evidence is contradictory or canonical target is not unique"})
    # Keep the list human-sized and deduplicated by the factual question.
    seen: set[str] = set()
    result = []
    for item in questions:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def render_plan_json(plan: DryRunPlan) -> dict[str, Any]:
    actions = Counter(item.get("action") for item in plan.assignments)
    classifications = Counter(item.get("classification") for item in plan.assignments)
    unresolved = len(plan.human_confirmation_required) + sum(1 for issue in plan.issues if issue.kind in {"ambiguous_identity", "ambiguous_group", "source_contradiction", "manual_pending"})
    return _json({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only",
        "report_date": REPORT_DATE,
        "source_tabs": plan.source_tabs,
        "source_rows": plan.source_rows,
        "canonical_students_inspected": plan.canonical_students_inspected,
        "production_source_snapshots": _json(plan.source_tabs.get("production_source_snapshots", [])),
        "current_active_memberships": sum(1 for item in plan.assignments if item.get("action") in {"KEEP", "DUPLICATE", "PROTECTED", "DEACTIVATE"}),
        "expected_active_memberships": len(plan.expected),
        "summary": {"KEEP": actions.get("KEEP", 0), "CREATE": actions.get("CREATE", 0), "DEACTIVATE": actions.get("DEACTIVATE", 0), "PROTECTED": actions.get("PROTECTED", 0), "UNRESOLVED": unresolved, "DUPLICATE": classifications.get("DUPLICATE", 0), "WRONG_GROUP": classifications.get("WRONG_GROUP", 0), "STALE": classifications.get("STALE", 0), "ambiguous_identities": sum(1 for issue in plan.issues if issue.kind == "ambiguous_identity"), "ambiguous_groups": sum(1 for issue in plan.issues if issue.kind == "ambiguous_group"), "manual_pending": sum(1 for issue in plan.issues if issue.kind == "manual_pending"), "source_contradictions": sum(1 for issue in plan.issues if issue.kind == "source_contradiction"), "invariant_violations": sum(1 for item in plan.invariants if item["status"] == "FAIL"), "computed_audiences_intentionally_skipped": len(plan.computed_audiences_skipped)},
        "assignments": plan.assignments,
        "selection_reconciliation": plan.selections,
        "invariants": plan.invariants,
        "issues": [{"kind": item.kind, "reason": item.reason, "details": item.details} for item in plan.issues],
        "computed_audiences_skipped": plan.computed_audiences_skipped,
        "human_confirmation_required": plan.human_confirmation_required,
        "proposed_group_preparations": plan.proposed_group_preparations,
        "known_edge_cases": plan.known_edge_cases,
        "literature_11_base_recheck": plan.literature_base_recheck,
        "production_writes_performed": 0,
        "write_guard": {"student_memberships_inserted": 0, "student_memberships_updated": 0, "student_memberships_deactivated": 0, "identities_created_or_changed": 0, "groups_created_or_changed": 0, "teacher_assignments_changed": 0},
    })


def render_plan_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        f"# Student Membership Dry-Run — {REPORT_DATE}",
        "",
        "Режим: **READ-ONLY**. Production apply не выполнялся.",
        "",
        "## Summary",
        "",
        f"- Canonical students inspected: **{payload['canonical_students_inspected']}**",
        f"- Current active memberships: **{payload['current_active_memberships']}**",
        f"- Expected active memberships: **{payload['expected_active_memberships']}**",
        f"- KEEP: **{summary['KEEP']}**; CREATE: **{summary['CREATE']}**; DEACTIVATE: **{summary['DEACTIVATE']}**; PROTECTED: **{summary['PROTECTED']}**; UNRESOLVED: **{summary['UNRESOLVED']}**",
        f"- Duplicate memberships: **{summary['DUPLICATE']}**; wrong-group: **{summary['WRONG_GROUP']}**; stale: **{summary['STALE']}**",
        f"- Ambiguous identities: **{summary['ambiguous_identities']}**; manual identity pending: **{summary.get('manual_pending', 0)}**; ambiguous groups: **{summary['ambiguous_groups']}**; source contradictions: **{summary['source_contradictions']}**",
        f"- Invariant violations: **{summary['invariant_violations']}**",
        f"- Computed audiences intentionally skipped: **{summary['computed_audiences_intentionally_skipped']}**",
        f"- Production source snapshot rows inspected: **{len(payload.get('production_source_snapshots') or [])}**",
        "- Production writes performed: **0**",
        "",
        "## Invariants",
        "",
    ]
    for item in payload["invariants"]:
        lines.append(f"- **{item['name']}** — {item['status']}. {json.dumps(item.get('details'), ensure_ascii=False, default=str)}")
    lines += ["", "## Human confirmation required", ""]
    if payload["human_confirmation_required"]:
        for item in payload["human_confirmation_required"]:
            lines += [f"### {item.get('student_or_group') or 'source issue'}", "", f"- {item['question']}", f"- Why code cannot choose: {item['why_code_cannot_choose']}", f"- Evidence: `{json.dumps(item['sources'], ensure_ascii=False, default=str)}`", ""]
    else:
        lines.append("Нет вопросов, требующих ручного выбора.")
    lines += ["", "## Computed audiences not materialized", ""]
    for item in payload["computed_audiences_skipped"]:
        lines.append(f"- {item['rule']} — {item['reason']}.")
    lines += ["", "## Literature 11 Base recheck", ""]
    lit = payload.get("literature_11_base_recheck") or {}
    lines.append(f"- Current: **{lit.get('current_membership_count', 0)}**, expected: **{lit.get('expected_membership_count', 0)}**.")
    lines.append(f"- Current-only: {', '.join(lit.get('current_only') or []) or '—'}; expected-only: {', '.join(lit.get('expected_only') or []) or '—'}.")
    lines += ["", "## Known edge cases", ""]
    for edge in payload.get("known_edge_cases") or []:
        lines.append(f"- **{edge['student']}** — {edge['result']}; current: {', '.join(edge.get('current_active_groups') or []) or '—'}; expected: {', '.join(edge.get('expected_groups') or []) or '—'}.")
    lines += ["", "## Future structural preparation", ""]
    for item in payload.get("proposed_group_preparations") or []:
        lines.append(f"- `{item.get('name')}` — group creation is proposed for a future apply pass only; production was not changed.")
    lines += ["", "## Proposed deactivations", "", "Каждая строка ниже также полностью сохранена в JSON с membership id, provenance, source ref, stale reason, authoritative source, structural rule, confidence и why-safe.", "", "| Student | Group | Membership | Source ref | Classification |", "|---|---|---|---|---|"]
    for item in payload.get("assignments") or []:
        if item.get("action") != "DEACTIVATE":
            continue
        lines.append(f"| {item.get('student','—')} | {item.get('group','—')} | `{item.get('membership_id','—')}` | {item.get('current_source_ref','—')} | {item.get('classification','—')} |")
    lines += ["", "## Assignment diff", "", "Подробный machine-readable diff находится в JSON рядом с этим отчётом.", "", "## Safety", "", "- Не создавались и не менялись identities/groups.", "- Не менялись teacher assignments.", "- Не изменялись memberships и validity periods.", "- Следующий apply-pass должен быть отдельным явно разрешённым действием и не должен принимать JSON как безусловную команду.", ""]
    return "\n".join(lines)


def fetch_authoritative_sources(settings: Settings) -> tuple[dict[str, Sequence[Sequence[Any]]], dict[str, Any]]:
    token_store = GoogleTokenStore()
    token = token_store.load()
    if not token:
        raise GoogleLiveError("Stored Google token is required for read-only student dry-run")
    client = GoogleLiveClient(google_config(settings), token, token_store)
    spreadsheet_id = settings.google_sheets_spreadsheet_id or ""
    metadata = client.spreadsheet(spreadsheet_id)
    title = str((metadata.get("properties") or {}).get("title", ""))
    titles = [str((sheet.get("properties") or {}).get("title", "")) for sheet in metadata.get("sheets") or []]
    resolved: dict[str, str] = {}
    ranges: list[str] = []
    requested = [CLASS_TAB, GROUP_TAB, EXAM_TAB, RULE_TAB]
    for requested_title in requested:
        actual = next((item for item in titles if item.casefold() == requested_title.casefold()), None)
        if not actual:
            raise GoogleLiveError(f"Authoritative tab not found: {requested_title}")
        resolved[requested_title] = actual
        ranges.append(f"'{actual.replace(chr(39), chr(39) + chr(39))}'!A:Z")
    values = client.sheet_values_many(spreadsheet_id, ranges)
    return dict(zip(requested, values)), {"spreadsheet_title": title, "spreadsheet_id": spreadsheet_id, "resolved_tabs": resolved}


def run_live_dry_run(database: Database, settings: Settings) -> dict[str, Any]:
    source_rows, source_meta = fetch_authoritative_sources(settings)
    snapshot = load_production_snapshot(database)
    manual_resolutions = load_manual_resolutions()
    expected, issues, diagnostics = build_expected_memberships(source_rows, snapshot, manual_resolutions)
    plan = reconcile_memberships(expected, snapshot, issues, source_rows, manual_resolutions)
    plan.source_tabs.update(source_meta)
    plan.source_tabs["parser_diagnostics"] = diagnostics
    plan.source_tabs["production_source_snapshots"] = snapshot.source_snapshots
    plan.source_tabs["manual_resolutions"] = {"version": manual_resolutions.get("version"), "decision_date": manual_resolutions.get("decision_date"), "confirmed_by": manual_resolutions.get("confirmed_by"), "path": "docs/STUDENT_MEMBERSHIP_MANUAL_RESOLUTIONS_2026-09-08.json"}
    return render_plan_json(plan)
