-- Cover School Directory foreign keys used by review/history joins and FK checks.
create index if not exists school_source_snapshots_sync_run_idx on public.school_source_snapshots(sync_run_id);
create index if not exists school_source_snapshots_previous_idx on public.school_source_snapshots(previous_snapshot_id);
create index if not exists school_candidate_changes_source_record_idx on public.school_candidate_changes(source_record_id);
create index if not exists school_candidate_changes_decision_by_idx on public.school_candidate_changes(decision_by);
create index if not exists school_resolution_issues_sync_run_idx on public.school_resolution_issues(sync_run_id);
create index if not exists school_resolution_issues_source_record_idx on public.school_resolution_issues(source_record_id);
create index if not exists school_resolution_issues_candidate_idx on public.school_resolution_issues(candidate_change_id);
create index if not exists school_resolution_issues_resolved_by_idx on public.school_resolution_issues(resolved_by);
create index if not exists school_source_mappings_identity_idx on public.school_source_mappings(identity_id);
create index if not exists school_source_mappings_group_idx on public.school_source_mappings(group_id);
create index if not exists school_source_mappings_classroom_course_idx on public.school_source_mappings(classroom_course_id);
create index if not exists school_source_mappings_created_by_idx on public.school_source_mappings(created_by);
create index if not exists school_source_mappings_supersedes_idx on public.school_source_mappings(supersedes_mapping_id);
