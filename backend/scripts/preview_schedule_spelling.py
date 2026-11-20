"""Prepare a read-only, revision-bound list of spelling edits from a full draft backup."""
import argparse
import hashlib
import json
from pathlib import Path

from backend.services.schedule_rooms import room_label
from backend.services.schedule_subjects import subject_label


def preview(raw, *, draft_id, revision, checksum):
    digest = hashlib.sha256(raw).hexdigest()
    if digest != checksum:
        raise ValueError("Контрольная сумма резервной копии не совпала.")
    draft = json.loads(raw)
    if (draft["id"], draft["revision"]) != (draft_id, revision):
        raise ValueError("Шаблон или редакция отличаются от запрошенных.")
    edits = []
    lessons = draft["payload"]["lessons"]
    if len({lesson["id"] for lesson in lessons}) != len(lessons):
        raise ValueError("В резервной копии повторяются идентификаторы занятий.")
    for lesson in lessons:
        for field, display in (("room", room_label), ("subject", subject_label)):
            old = lesson.get(field)
            if old and old != (new := display(old)):
                edits.append({"lesson_id": lesson["id"], "field": field, "before": old, "after": new})
    return {"draft_id": draft_id, "expected_revision": revision, "backup_sha256": digest,
            "lessons": len(lessons), "edits": edits}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--draft-id", required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(preview(args.backup.read_bytes(), draft_id=args.draft_id,
                             revision=args.revision, checksum=args.sha256), ensure_ascii=False, indent=2))
