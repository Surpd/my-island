-- Synthetic rows inserted before migrations 054–058.
INSERT INTO identities (id,kind,display_name) VALUES
 ('10000000-0000-0000-0000-000000000001','student','Fixture student'),
 ('10000000-0000-0000-0000-000000000002','teacher','Fixture teacher');
INSERT INTO users (id,telegram_user_id,role) VALUES
 ('10000000-0000-0000-0000-000000000003',60000001,'admin');
INSERT INTO groups (id,name,group_type,canonical,base_class_name) VALUES
 ('10000000-0000-0000-0000-000000000004','Fixture 9A','class',true,'9A');
INSERT INTO memberships (id,group_id,identity_id,member_role,source)
 VALUES ('10000000-0000-0000-0000-000000000005','10000000-0000-0000-0000-000000000004',
 '10000000-0000-0000-0000-000000000001','student','admin_override');
INSERT INTO journal_sources (id,spreadsheet_id,spreadsheet_title,grade,subject,sheet_title)
 VALUES ('10000000-0000-0000-0000-000000000006','synthetic-book','Fixture journal',9,'Math','Fixture tab');
INSERT INTO schedule_experiment_drafts (id,name,revision,payload,created_by)
 VALUES ('fixture-draft','Fixture draft',3,'{"week_start":"2026-12-28","draft_kind":"school"}',
 '10000000-0000-0000-0000-000000000003');
INSERT INTO journal_assessments (id,source_id,external_key,title,assessed_on,weight,max_score,source_column)
 VALUES ('10000000-0000-0000-0000-000000000007','10000000-0000-0000-0000-000000000006',
 'fixture-work','Fixture work','2026-11-23',2,10,'B');
INSERT INTO journal_results (id,assessment_id,identity_id,student_name,group_marker,numeric_score,source_coordinate)
 VALUES ('10000000-0000-0000-0000-000000000008','10000000-0000-0000-0000-000000000007',
 '10000000-0000-0000-0000-000000000001','Fixture student','all',0,'B5');
