# Grade 7 Directory / Membership Migration — Dry Run

Date: 2026-09-13  
Mode: `READ_ONLY` — generated before apply

## Authoritative source

- Google spreadsheet: `Расписание  2026/27`
- Spreadsheet ID: `1j5dI2VbT0RoW2A5ZkCpMYVdt7cSY4ZjNxQaMWtu--Fo`
- Class roster: `Списки по классам 26/27!E1:H15`
- Shared split roster: `списки групп 26-27!E4:I20`, Mathematics, `7-1 Иван` / `7-2 Иван`
- English roster: `списки групп 26-27!E22:K42`, groups `3`, `4`, `5`
- Class roster fingerprint: `a5001cf8485e616a`
- Group roster fingerprint: `f7bdd7f53c013eb5`

## Accepted semantic model

```text
base_class: 7-А | 7-Б
shared_split: 1 | 2
english_group: 3 | 4 | 5
elective/special memberships
```

## Group diff

| Existing / new | ID policy | Before | After | Role | Semantic dimension |
|---|---|---|---|---|---|
| existing split 1 | preserve ID `824dacf3-28dd-4342-8c62-8f3a94b7337f` | `7-1` / class | `grade7:split:1` / instructional partition | `instructional_partition` | `grade7_shared_split` |
| existing split 2 | preserve ID `84e97d6d-f596-4dec-8053-0a98b1a39339` | `7-2` / class | `grade7:split:2` / instructional partition | `instructional_partition` | `grade7_shared_split` |
| new base | new ID | — | `grade7:base:7-А` | `base_class` | `grade7_base_class` |
| new base | new ID | — | `grade7:base:7-Б` | `base_class` | `grade7_base_class` |

Groups: **51 → 53**. No other group is in scope.

## Identity reconciliation

All four variants resolve to one existing active identity; no identity is created.

| Source spelling | Existing canonical identity | Evidence | Result |
|---|---|---|---|
| `Коченкова Таисия` (`списки групп 26-27!F9`) | `Коченкова Тая` | same surname; `таисия` ↔ `тая`; current class roster `7-А!F6`; one active candidate | resolved to existing ID `514f7fc5-3a16-4586-ba68-0d640a1fd42a` |
| `Кудимов Петр` (`списки групп 26-27!F10`) | `Кудимов Петя` | same surname; `петр` ↔ `петя`; current class roster `7-А!F9`; one active candidate | resolved to existing ID `0952b3f6-0fa3-40c0-969b-8e1b10135fc4` |
| `Крылов Николай` (`списки групп 26-27!H12`) | `Крылов Коля` | same surname; `николай` ↔ `коля`; current class roster `7-А!F7`; one active candidate | resolved to existing ID `152e81bb-0533-46dc-8e2d-c5fadfc30767` |
| `Шишков Гриша` (`списки групп 26-27!F16`) | `Шишков Григорий` | same surname; `гриша` ↔ `григорий`; current class roster `7-Б!H13`; one active candidate | resolved to existing ID `b63b5767-901d-4eb9-8a73-e02f7161de0f` |

`Коуттс Александра` (`cf187afb-919c-4f00-bcdd-0e7cdd3ac538`) is active in DB with old `class_name=7-2`, but is absent from both current class and split rosters, has no active memberships and no user account. Dry-run action: no class/split assignment; clear stale class metadata and mark the identity for manual directory review/inactive candidate. No automatic inference.

## Membership diff

### Base class memberships

- `CREATE 24`: 12 from `Списки по классам 26/27!F2:F13` → `grade7:base:7-А`; 12 from `...!H2:H13` → `grade7:base:7-Б`.
- `DEACTIVATE 0`.
- Source is the current class roster only.

### Shared split memberships

- `KEEP 12`: 8 existing `7-1` relations and 4 existing `7-2` relations whose source reference already matches the current Mathematics roster.
- `CREATE 12`: 3 corrected/new relations for split 1 and 9 corrected/new relations for split 2, each with current Mathematics roster provenance.
- `DEACTIVATE 12`: 3 stale split-1 rows and 9 stale split-2 rows, including wrong-group rows and rows whose old source reference does not describe the current split roster. Physical delete: none; validity history retained.
- Final active split counts: `1 = 11`, `2 = 13`, overlap `0`.

### English memberships

- Grade 7 source sets are already exact: English `3 = 9`, `4 = 8`, `5 = 6`.
- `CREATE 0`, `DEACTIVATE 0` for English Grade 7.
- English 3/4/5 remain independent from base class and shared split.

## Dry-run totals

```text
Groups:              51 → 53
Memberships CREATE:  36  (24 base + 12 split)
Memberships KEEP:    12  (split, exact current source provenance)
Memberships DEACT:   12  (history retained, no physical deletes)
Identity aliases:     4 resolved
Identity creates:     0
Manual review:        1 (Коуттс Александра)
New ambiguity:         0
Canonical v1:          unchanged
Production UI:         unchanged
```

Apply gate: **PASS**. The diff is source-backed and contains no unresolved alias or group ambiguity. The only manual-review identity is deliberately excluded from the target Grade 7 directory.
