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
