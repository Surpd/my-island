from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
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
    system_instruction: str | None = None
    response_schema: Mapping[str, Any] | None = None
    visual_context: tuple[Mapping[str, Any], ...] = ()


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


class GroqSemanticProvider:
    """Optional backend-only Groq fallback.

    The provider returns a proposal only.  Callers must validate it with the
    deterministic reconciliation gate before touching canonical data.
    """

    provider_name = "groq"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, timeout: float = 15.0):
        self._api_key = os.getenv("GROQ_API_KEY", "") if api_key is None else api_key
        self._configured_model = os.getenv("GROQ_MODEL", "") if model is None else model
        self.timeout = timeout
        self._model = self._configured_model or ""
        self._models: list[str] = []

    @property
    def model_name(self) -> str:
        return self._model or "runtime-selected"

    def available_models(self) -> list[str]:
        if not self._api_key:
            return []
        request = urllib.request.Request(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]
        self._models = models
        if self._configured_model and self._configured_model in models:
            self._model = self._configured_model
        elif models:
            preferred_order = (
                "openai/gpt-oss-120b", "openai/gpt-oss-20b",
                "llama-3.3-70b-versatile", "qwen/qwen3-32b",
            )
            self._model = next((item for item in preferred_order if item in models), sorted(models)[0])
        return models

    def interpret(self, request: SemanticRequest) -> SemanticResponse:
        if not self._api_key:
            raise RuntimeError("GROQ_API_KEY is not configured")
        if not self._model:
            self.available_models()
        if not self._model:
            raise RuntimeError("Groq returned no usable models")
        if request.visual_context:
            if not self._models:
                self.available_models()
            configured_vision = os.getenv("GROQ_VISION_MODEL", "")
            vision_candidates = (
                configured_vision,
                "qwen/qwen3.6-27b",
                "qwen/qwen3.8-27b",
            )
            if not any(token in self._model.casefold() for token in ("vision", "qwen3.6", "qwen3.8")):
                self._model = next((item for item in vision_candidates if item and item in self._models), "")
            if not self._model:
                raise RuntimeError("No configured Groq vision model is available")
        prompt = {
            "source_type": request.source_type,
            "record_key": request.record_key,
            "structural_payload": request.structural_payload,
            "raw_payload": request.raw_payload,
            "instruction": (
                "Return an object that conforms exactly to the supplied JSON schema."
                if request.response_schema
                else "Return JSON only: identity_id, confidence, evidence, reason. Do not invent identities."
            ),
        }
        user_content: Any = json.dumps(prompt, ensure_ascii=False)
        if request.visual_context:
            user_content = [{"type": "text", "text": user_content}]
            for visual in request.visual_context:
                data_url = str(visual.get("data_url") or "")
                if not data_url.startswith("data:image/"):
                    raise ValueError("Visual semantic context must be an image data URL")
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": data_url, "detail": str(visual.get("detail") or "high")},
                })
        response_format: Mapping[str, Any]
        if request.response_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "semantic_response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        body = json.dumps({
            "model": self._model,
            "temperature": 0,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": request.system_instruction or "You are a conservative school-directory reconciliation assistant."},
                {"role": "user", "content": user_content},
            ],
        }).encode("utf-8")
        request_obj = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request_obj, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return SemanticResponse(self.provider_name, self._model, json.loads(content))


def validate_candidate_change(candidate: Mapping[str, Any]) -> None:
    allowed_entities = {
        "person", "group", "membership", "teacher_assignment", "homeroom_assignment",
        "source_mapping", "schedule_audience", "selection_fact",
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
