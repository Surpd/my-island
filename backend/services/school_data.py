from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def diff_source_records(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return a deterministic record diff; only new/changed units need semantics."""
    changes: list[dict[str, Any]] = []
    for key in sorted(set(previous) | set(current)):
        before = previous.get(key)
        after = current.get(key)
        if key not in current:
            kind = "deleted"
            value = before
        elif key not in previous:
            kind = "new"
            value = after
        elif stable_fingerprint(before) == stable_fingerprint(after):
            kind = "unchanged"
            value = after
        else:
            kind = "changed"
            value = after
        changes.append({"record_key": key, "change_kind": kind, "fingerprint": stable_fingerprint(value), "payload": value})
    return changes


@dataclass(frozen=True)
class SemanticRequest:
    source_type: str
    record_key: str
    structural_payload: Mapping[str, Any]
    raw_payload: Any


@dataclass(frozen=True)
class SemanticResponse:
    provider: str
    model: str
    payload: Mapping[str, Any]


class SemanticProvider(Protocol):
    """Runtime boundary implemented by Groq; model choice stays configuration."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def interpret(self, request: SemanticRequest) -> SemanticResponse: ...


def validate_candidate_change(candidate: Mapping[str, Any]) -> None:
    allowed_entities = {
        "person", "group", "membership", "teacher_assignment", "homeroom_assignment",
        "source_mapping", "schedule_audience",
    }
    if candidate.get("entity_type") not in allowed_entities:
        raise ValueError("Unsupported School Directory candidate entity")
    if candidate.get("change_type") not in {"create", "update", "end", "map"}:
        raise ValueError("Unsupported School Directory candidate change")
    if not str(candidate.get("natural_key") or "").strip():
        raise ValueError("Candidate natural_key is required")
    if not isinstance(candidate.get("proposed_payload"), Mapping):
        raise ValueError("Candidate proposed_payload must be structured")
    evidence = candidate.get("evidence")
    if not isinstance(evidence, Mapping) or not evidence.get("source_ref"):
        raise ValueError("Candidate source evidence is required")


def validate_audience_rule(rule: Mapping[str, Any]) -> None:
    """Validate the extensible rule shape without resolving memberships yet."""
    kind = rule.get("type")
    if kind == "cohort":
        if not rule.get("group_id"):
            raise ValueError("Cohort audience needs group_id")
        return
    if kind in {"union", "intersection"}:
        children = rule.get("rules")
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)) or not children:
            raise ValueError(f"{kind} audience needs rules")
        for child in children:
            if not isinstance(child, Mapping):
                raise ValueError("Audience child rule must be structured")
            validate_audience_rule(child)
        return
    if kind in {"exclude", "complement"}:
        base = rule.get("base")
        excluded = rule.get("exclude")
        if not isinstance(base, Mapping) or not isinstance(excluded, Mapping):
            raise ValueError(f"{kind} audience needs base and exclude rules")
        validate_audience_rule(base)
        validate_audience_rule(excluded)
        return
    if kind == "explicit":
        identity_ids = rule.get("identity_ids")
        if not isinstance(identity_ids, Sequence) or isinstance(identity_ids, (str, bytes)) or not identity_ids:
            raise ValueError("Explicit audience needs identity_ids")
        return
    raise ValueError("Unsupported schedule audience rule")
