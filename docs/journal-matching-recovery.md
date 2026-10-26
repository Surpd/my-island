# Journal association release and recovery

Migration `045_journal_entry_matching.sql` adds a team discriminator and a private participant junction. It classifies only the explicitly reviewed 13 existing art rows, guarded by the workbook/sheet identity, 37 current results, absent personal links, and the workbook import lock. It does not recreate rows or modify results/history.

Before production application, retain a private JSON snapshot of every workbook result and the original student association fields. The release check compares the complete result rows and original associations after migration and after a repeat import. The snapshot lives in ignored `data/`; never commit student data or credentials.

## Classification recovery

Stop if an import is running. Acquire the same `journal-book:<spreadsheet_id>` advisory transaction lock. Inspect the exact art source and the 13 rows with `classification_method='user_confirmed_art_team_roster'`. If participants have since been assigned, preserve their audit and current junction rows before any reversal. Restore only those rows' previous `entry_kind` and `classification_method` from the approved baseline; for this initial migration they were `individual` and NULL. Keep the additive schema, original row IDs, results, history, and participant records. Do not roll the application back to code that treats team results as individual grades while teams remain classified.

## Confirmed association recovery

Each confirmation is atomic and writes `journal_matching.confirmed` to `audit_log`, including previous student/result association IDs and the prior alias. To reverse an erroneous administrator confirmation, first acquire the workbook lock and inspect later audit events. Refuse a blind reversal if any affected association or alias has changed since that confirmation. Restore only the association fields recorded in that event; revoke the newly created alias and restore the previous mapping's validity if appropriate. Never modify score values or result history. Preview and validate the proposed reversal before applying it.

Team participant edits use `journal_team.members_changed`, preserving previous and new participant IDs. Their reversal affects only the junction; individual result identities stay NULL for teams.

## Import behavior

Imports preserve `entry_kind`, participant membership and manually confirmed source aliases. Suggestions never write aliases. New ambiguous rows remain unresolved individuals until reviewed: the importer does not infer teams from conjunctions in names. Merged/archived people cannot be confirmation targets. A fingerprint ties confirmation to the reviewed source rows, result associations, canonical identity/groups and alias state; stale previews are rejected.
