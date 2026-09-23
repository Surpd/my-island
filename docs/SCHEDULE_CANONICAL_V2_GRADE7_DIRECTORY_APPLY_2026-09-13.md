# Grade 7 Directory / Membership Migration — Apply and Live Shadow Report

Date: 2026-09-13  
Mode: controlled apply + read-only live shadow validation

## Scope guard

- Canonical v1 was not changed.
- The Grade 7 candidate JSON was not changed.
- No production UI or authoritative schedule switch was performed.
- The effective week was materialized for `2026-09-14` with `patch_count=0`; no weekly overlay was added.
- The historical week `2026-09-07` was not used as a validator.

## Directory read-back

| Entity | Before | After | ID / result |
|---|---:|---:|---|
| Groups | 51 | 53 | two existing split IDs preserved; two base groups created |
| `grade7:base:7-А` | — | 12 active | `77433628-3ed6-49ec-b3b5-ede3d7862031` |
| `grade7:base:7-Б` | — | 12 active | `c930621f-890c-4cdb-a420-cec6d39a2307` |
| `grade7:split:1` | `7-1`, 11 | 11 active | `824dacf3-28dd-4342-8c62-8f3a94b7337f` preserved |
| `grade7:split:2` | `7-2`, 13 | 13 active | `84e97d6d-f596-4dec-8053-0a98b1a39339` preserved |
| split overlap | — | 0 | passed |
| active identities with old class name `7-1/7-2` | — | 0 | passed |

Group roles/dimensions are `base_class/grade7_base_class` for the two class groups and `instructional_partition/grade7_shared_split` for split 1/2.

## Membership reconciliation

| Operation | Count | Evidence |
|---|---:|---|
| CREATE | 36 | 24 current class-roster memberships + 12 current split-roster corrections/new relations |
| KEEP | 12 | existing split relations whose source references already matched current roster |
| DEACTIVATE | 12 | stale/wrong split relations; no physical deletes |
| physical deletes | 0 | historical provenance retained |

English Grade 7 membership read-back against the current roster: `english:3=9`, `english:4=8`, `english:5=6`. English groups remain a separate instructional dimension; no base/split membership was used to construct them.

## Identity reconciliation

No identities were created. Four short-name aliases resolved to existing identities:

| Source | Existing identity | Evidence |
|---|---|---|
| `Коченкова Таисия`, `списки групп 26-27!F9` | `Коченкова Тая` | same surname, registered name variant, current 7-А + split context |
| `Кудимов Петр`, `списки групп 26-27!F10` | `Кудимов Петя` | same surname, registered name variant, current 7-А + split context |
| `Крылов Николай`, `списки групп 26-27!H12` | `Крылов Коля` | same surname, registered name variant, current 7-А + split context |
| `Шишков Гриша`, `списки групп 26-27!F16` | `Шишков Григорий` | same surname, registered name variant, current 7-Б + split context |

The extra active identity `Коуттс Александра` was not present in current class or split rosters. It was not assigned to a class or split: `class_name=NULL`, `status=inactive`, and an audit/manual-review record was retained.

## Candidate persistence

- Version: `candidate-v2-grade7-fd6d3f3beccca0fc`
- Parent: `4d21ef71ba1b0cb92b48735f410758b64a882c5f496a4361be02c5a6e229cf93`
- Persisted status: `approved_with_exceptions`
- Authoritative: `false` (stored in metadata; no authoritative switch)
- Source snapshot: spreadsheet `1j5dI2VbT0RoW2A5ZkCpMYVdt7cSY4ZjNxQaMWtu--Fo`, fingerprint `c4532841c9daf9a7`
- Persisted blocks: `39`
- Persisted assignments: `61`
- Base-class assignments resolved to canonical group IDs: `28`
- Source-only assignments left unresolved in persistence: `0`

Canonical v1 read-back remains `approved_with_exceptions` with source fingerprint `c145928d9058cb6bbe6d30283d3eedc97300c5f7cbae5724eca19e2862cf5699`.

## Live Grade 7 projection

Effective week: `2026-09-14`, effective week id `bd7c399a71ff3b3d965745903500d60d4d7f7e4fcc6ba677dd073de97c1cf807`.

```text
24 active students × 39 blocks = 936 projected student-block items
ACTIVITY   693
NO_LESSON  243
UNRESOLVED 0
```

Validation gates:

```text
duplicate student-slot allocations = 0
multiple distinct activities       = 0
unknown group refs                 = 0
unexplained unresolved             = 0
```

The candidate contains no teacher IDs, so teacher projection has no source assignments to project; this is source-data absence, not a teacher conflict or runtime failure.

## Verdict

`READY_FOR_CURRENT_TEMPLATE_V2_BOOTSTRAP`

The database now represents `7-А/7-Б` as base classes, `1/2` as an independent shared split, and English `3/4/5` as an independent instructional partition. The persisted version remains a non-authoritative candidate and is ready for the current-template V2 bootstrap, not for production UI activation.

