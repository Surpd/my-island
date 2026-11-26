"""PostgreSQL integration fixture for historical group-only class assignments."""
import os
from time import perf_counter
from unittest import TestCase, skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

from backend.database import Database
from backend.services.class_load_analytics import class_load_analytics
from backend.services.teacher_load_analytics import teacher_load_analytics


@skipUnless(os.getenv("CLASS_LOAD_TEST_DATABASE_URL"), "requires dedicated PG17 database")
class PostgresClassLoadTests(TestCase):
    def test_batch_reads_historical_assignments_without_identity_table(self):
        dsn = os.environ["CLASS_LOAD_TEST_DATABASE_URL"]
        parsed = urlsplit(dsn)
        self.assertIn(parsed.hostname, ("localhost", "127.0.0.1"))
        self.assertEqual((parsed.port,parsed.path),(55460,"/my_island_class_load_test"))
        db = Database(database_url=dsn)
        with db.connection() as conn:
            version = conn.execute("SHOW server_version_num").fetchone()["server_version_num"]
            self.assertEqual(int(version)//10000,17)
            conn.execute("""CREATE TABLE groups(id TEXT PRIMARY KEY,name TEXT,display_name TEXT,
                group_type TEXT,base_class_name TEXT,subject TEXT,subject_subgroup TEXT,exam_track TEXT,canonical BOOLEAN)""")
            conn.execute("""CREATE TABLE memberships(group_id TEXT,identity_id TEXT,active BOOLEAN,
                member_role TEXT,valid_from DATE,valid_until DATE)""")
            conn.execute("""CREATE TABLE identities(id TEXT PRIMARY KEY,display_name TEXT,status TEXT,kind TEXT)""")
            conn.execute("INSERT INTO identities VALUES('teacher','Synthetic teacher','active','teacher')")
            conn.execute("INSERT INTO groups(id,name,group_type,canonical) VALUES('g','9A','class',true)")
            conn.execute("""INSERT INTO canonical_schedule_versions(version_id,source_snapshot_id,source_fingerprint,status)
                VALUES('v','s','fp','authoritative')""")
            conn.execute("""INSERT INTO canonical_schedule_blocks(version_id,block_key,weekday,start_time,end_time,status)
                SELECT 'v','b-' || i,0,'09:15'::time,'10:00'::time,'resolved' FROM generate_series(1,960) i""")
            conn.execute("""INSERT INTO canonical_schedule_assignments(block_id,ordinal,activity,canonical_group_ids,teacher_ids)
                SELECT id,0,'Lab','["g"]'::jsonb,'["teacher"]'::jsonb FROM canonical_schedule_blocks""")
            conn.execute("""INSERT INTO canonical_effective_weeks(effective_week_id,version_id,week_start,overlay_fingerprint)
                VALUES('wk','v','2026-12-28','overlay')""")
            conn.execute("""INSERT INTO canonical_effective_blocks(effective_week_id,canonical_block_id,block_key,change_kind)
                SELECT 'wk',id,block_key,'unchanged' FROM canonical_schedule_blocks""")
        started = perf_counter()
        with patch.object(db, "execute", wraps=db.execute) as statements:
            report = class_load_analytics(db,"2026-12-28","2027-01-03")
        self.assertLessEqual(statements.call_count, 9)
        group, = report["groups"]
        self.assertIsNone(group["scheduled_minutes"])
        self.assertEqual((group["observed_lesson_count"], group["observed_scheduled_minutes"],
                          group["observed_assignment_minutes"]), (960, 45, 43200))
        print(f"PG17 class: 960 assignments, {statements.call_count} SQL calls, {(perf_counter()-started)*1000:.1f} ms.")
        db = Database(database_url=dsn)
        started = perf_counter()
        with patch.object(db, "execute", wraps=db.execute) as statements:
            teacher_report = teacher_load_analytics(db, "2026-12-28", "2027-01-03")
        self.assertLessEqual(statements.call_count, 12)
        teacher, = teacher_report["teachers"]
        self.assertEqual((teacher["observed_scheduled_minutes"], teacher["observed_occupied_minutes"],
                          teacher["observed_conflict_minutes"]), (43200, 45, 45))
        print(f"PG17 teacher: 960 assignments, {statements.call_count} SQL calls, {(perf_counter()-started)*1000:.1f} ms.")
