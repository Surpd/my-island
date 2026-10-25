"""Conservative suggestions and explicitly confirmed journal associations."""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from typing import Any


def normalized_name(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def belongs_to_grade(identity: dict[str, Any], grade: int) -> bool:
    labels = [identity.get("class_name")] + [g.get("base_class_name") or g.get("name") for g in identity.get("groups", [])]
    return any(re.match(rf"^{grade}(?:\D|$)", str(label or "")) for label in labels)


def suggestion_reason(source_name: str, target_name: str) -> str | None:
    source, target = normalized_name(source_name).split(), normalized_name(target_name).split()
    if len(source) != 2 or len(target) != 2:
        return None
    short_names = {"виктория": "вика", "екатерина": "катя", "петр": "петя", "михаил": "миша", "александр": "саша", "анастасия": "настя", "алексей": "леша", "мария": "маша", "дарья": "даша", "елизавета": "лиза", "николай": "коля", "василий": "вася", "константин": "костя", "даниил": "даня", "данила": "даня"}
    for left in (source, source[::-1]):
        for right in (target, target[::-1]):
            if left == right:
                return "Совпадают имя и фамилия; проверьте соответствие."
            if left[0] == right[0] and short_names.get(left[1], left[1]) == short_names.get(right[1], right[1]):
                return "Совпадает фамилия; полная и краткая формы имени. Требуется подтверждение."
            if left[1] == right[1] and SequenceMatcher(None, left[0], right[0]).ratio() >= 0.85:
                return "Совпадает имя; похожая фамилия. Требуется подтверждение."
    return None


def preview_fingerprint(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def matching_overview(database, spreadsheet_id):
    return database.journal_matching_overview(spreadsheet_id)


def preview_match(database, spreadsheet_id, source_name, identity_id):
    return database.preview_journal_match(spreadsheet_id, source_name, identity_id)


def confirm_match(database, spreadsheet_id, source_name, identity_id, preview_token, actor_user_id):
    return database.confirm_journal_match(spreadsheet_id, source_name, identity_id, preview_token, actor_user_id)


def set_team_members(database, team_id, identity_ids, actor_user_id):
    return database.set_journal_team_members(team_id, identity_ids, actor_user_id)
