# School Data v1 — Bootstrap + People Library + Journal/Schedule

## Contract

Canonical people and groups are independent of app accounts. Student membership, teacher assignment, explicit homeroom, schedule audience, and source mapping are separate temporal/provenance-aware relations. Grade 9 Math canonical names are A/B/C; `9-1`/`9-2`/`9-3` are source aliases only.

Use migrations `013`–`016` as the staging contract:

1. Register each inspected Google/Journal/Schedule/Classroom/manual source in `school_sources`; do not invent precedence while its semantics are unknown.
2. Save a raw + structural `school_source_snapshot`, split it deterministically into stable-keyed `school_source_records`, and compare fingerprints with the previous valid snapshot.
3. Ignore unchanged records. Parse obvious changes deterministically; send only bounded ambiguous units to a configured `SemanticProvider` (Groq initially, model replaceable).
4. Persist semantic request/output and deterministic validation diagnostics. An LLM output is evidence for a candidate, never authority to write canonical rows.
5. Create evidence-bearing `school_candidate_changes`. Auto-apply only exact, deterministic, non-conflicting changes; ambiguous identity, structure, deletion, or precedence becomes `school_resolution_issues`.
6. Preserve admin decisions as versioned, manually confirmed `school_source_mappings`; end/supersede mappings rather than rewriting history.
7. Apply memberships/assignments with validity periods. Memberships are students only; teacher and homeroom responsibilities use their own tables.
8. People Library reads should start from `identities`, optionally join the confirmed account link/roles, then fetch current memberships or assignments separately; expose provenance/history and open issues only in details.
9. Journal adapters normalize source rosters/results, resolve exact canonical identities or remain unlinked/ambiguous, and map source group markers explicitly. Official Journal grades remain separate from Classroom grades.
10. Schedule adapters isolate the class block before semantic work, build normalized lessons with `audience_rule`, resolve against current memberships, and preserve raw/structural/semantic diagnostics. Never reactivate migration `011` archives.

## Recommended order

Verify migrations `013`–`016`; implement snapshot/run repository methods; inspect and bootstrap current School Directory sources; add candidate review/apply service; build People Library read APIs; then integrate Journal and Schedule adapters. Benchmark current Groq models only after representative difficult source units are captured.

## Do not guess

Source precedence by grade/relationship; complete memberships for Иван Куренков; any Grade 11/base/profile relationships; homeroom; replacement vs additional semantics for unverified exam groups; combined/unknown source columns; schedule boundaries that deterministic extraction cannot prove; or Groq model/acceptance thresholds before benchmark fixtures exist.
