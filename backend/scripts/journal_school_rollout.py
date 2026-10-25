"""Private, bounded source capture and production rollout for school journals.

Source captures and database backups belong in ignored data/, never in Git.
"""
from __future__ import annotations

import argparse
import json
import sys
import gzip
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse
from pathlib import Path

from backend.config import get_settings
from backend.database import Database
from backend.services.google_sync_service import CURRENT_JOURNAL_SPREADSHEETS, _client
from backend.services.journal_import import column_name, parse_journal_values, sync_journal_values

BACKUP_TABLES = ('journal_sources','journal_assessments','journal_students','journal_results',
    'journal_result_history','journal_student_summaries','journal_summary_history','journal_sync_runs',
    'journal_group_mappings','journal_team_members','journal_term_settings','school_sources','school_source_mappings')


def validation_state(database: Database) -> dict:
    """Stable IDs, values and histories; exclude sync timestamps and raw transport."""
    queries = {
        'assessments': 'SELECT id,source_id,title,assessed_on,weight,max_score,active FROM journal_assessments ORDER BY id',
        'students': 'SELECT id,source_id,identity_id,entry_kind,active FROM journal_students ORDER BY id',
        'results': 'SELECT id,assessment_id,journal_student_id,identity_id,numeric_score,status,is_current FROM journal_results ORDER BY id',
        'summaries': 'SELECT id,journal_student_id,summary_type,numeric_value,text_value,is_current FROM journal_student_summaries ORDER BY id',
        'grade_history': 'SELECT * FROM journal_result_history ORDER BY id',
        'summary_history': 'SELECT * FROM journal_summary_history ORDER BY id',
        'mappings': 'SELECT * FROM school_source_mappings ORDER BY id',
    }
    with database.connection() as connection:
        return {name: hashlib.sha256(json.dumps([dict(row) for row in database.execute(connection,query).fetchall()],
            sort_keys=True,default=str).encode()).hexdigest() for name,query in queries.items()}


def production_database() -> Database:
    url = get_settings().database_url
    parsed = urlparse(url or '')
    if not url or not (parsed.hostname == 'db.kmrcbewmxeqqxfauiwyy.supabase.co' or
        parsed.hostname and parsed.hostname.endswith('.pooler.supabase.com') and (parsed.username or '').endswith('.kmrcbewmxeqqxfauiwyy')):
        raise RuntimeError('Refusing journal rollout outside the approved production Supabase project')
    return Database(database_url=url)


def backup(path: Path) -> None:
    database = production_database()
    snapshot = {'project_id':'kmrcbewmxeqqxfauiwyy','created_at':datetime.now(timezone.utc).isoformat(),'tables':{}}
    with database.connection() as connection:
        database.execute(connection, 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        for table in BACKUP_TABLES:
            snapshot['tables'][table] = [dict(row) for row in database.execute(connection,f'SELECT * FROM {table}').fetchall()]
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as output:
        json.dump(snapshot,output,ensure_ascii=False,default=str)
    print(json.dumps({'backup':str(path),'project_id':snapshot['project_id'],
        'rows':{table:len(rows) for table,rows in snapshot['tables'].items()},
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}),flush=True)


def report() -> None:
    database = production_database()
    with database.connection() as connection:
        rows = database.execute(connection, '''SELECT source.grade, COUNT(*) subjects,
            COUNT(*) FILTER (WHERE source.status='error') failed_subjects,
            (SELECT COUNT(DISTINCT st.identity_id) FROM journal_students st JOIN journal_sources s ON s.id=st.source_id WHERE s.grade=source.grade AND st.active IS TRUE AND st.entry_kind='individual') linked_students,
            (SELECT COUNT(DISTINCT lower(trim(st.display_name))) FROM journal_students st JOIN journal_sources s ON s.id=st.source_id WHERE s.grade=source.grade AND st.active IS TRUE AND st.entry_kind='individual' AND st.identity_id IS NULL) unresolved_names,
            (SELECT COUNT(*) FROM journal_assessments a JOIN journal_sources s ON s.id=a.source_id WHERE s.grade=source.grade AND a.active IS TRUE) works,
            (SELECT COUNT(*) FROM journal_results r JOIN journal_assessments a ON a.id=r.assessment_id JOIN journal_sources s ON s.id=a.source_id WHERE s.grade=source.grade AND r.is_current IS TRUE) results,
            COUNT(*) FILTER (WHERE source.auto_sync_enabled IS TRUE) automated_subjects,
            COUNT(*) FILTER (WHERE source.term_number=1 AND source.period_status='current') first_term_subjects
            FROM journal_sources source GROUP BY source.grade ORDER BY source.grade''').fetchall()
        summaries = database.execute(connection, '''SELECT summary_type,COUNT(*) values,
            COUNT(*) FILTER (WHERE numeric_value IS NOT NULL) numeric_values FROM journal_student_summaries
            WHERE is_current IS TRUE GROUP BY summary_type ORDER BY summary_type''').fetchall()
        integrity = database.execute(connection, '''SELECT
            (SELECT COUNT(*) FROM journal_results r JOIN journal_students s ON s.id=r.journal_student_id WHERE s.entry_kind='team' AND r.identity_id IS NOT NULL) team_identity_violations,
            (SELECT COUNT(*) FROM journal_result_history) grade_history,
            (SELECT COUNT(*) FROM journal_summary_history) summary_history,
            (SELECT COUNT(*) FROM journal_group_mappings) group_mappings,
            (SELECT COUNT(*) FROM journal_sync_runs WHERE status='running') running''').fetchone()
    print(json.dumps({'classes':[dict(row) for row in rows],'summaries':[dict(row) for row in summaries],
        'integrity':dict(integrity)},ensure_ascii=False,default=str),flush=True)


def import_capture(path: Path, backup_path: Path, *, repeat: bool = False) -> None:
    if not backup_path.exists():
        raise RuntimeError('A private pre-rollout backup is required')
    with gzip.open(backup_path,'rt',encoding='utf-8') as handle:
        if json.load(handle).get('project_id') != 'kmrcbewmxeqqxfauiwyy':
            raise RuntimeError('Backup target mismatch')
    database = production_database()
    before = validation_state(database) if repeat else None
    books = json.loads(path.read_text(encoding='utf-8'))
    selected = {(5,'Математика'),(8,'Проекты'),(11,'Математика')} if repeat else None
    with database.journal_workbook_lock('__school_import__') as available:
        if not available:
            raise RuntimeError('Another school journal import is active')
        for book in books:
            tabs = [tab for tab in book['tabs'] if selected is None or (book['grade'],tab['title'].strip()) in selected]
            if not tabs:
                continue
            key = book['spreadsheet_id']
            run = database.start_journal_sync(key,book['grade'],None,'manual')
            if run is None:
                raise RuntimeError('A workbook import is already active')
            outcomes = []
            try:
                with database.journal_workbook_lock(key) as acquired:
                    if not acquired:
                        raise RuntimeError('Workbook lock unavailable')
                    for tab in tabs:
                        try:
                            counts = sync_journal_values(database,tab['values'],spreadsheet_id=key,
                                spreadsheet_title=book['spreadsheet_title'],grade=book['grade'],subject=tab['title'].strip(),
                                sheet_title=tab['title'],sheet_id=tab['sheetId'])
                            outcomes.append({'status':'success','counts':counts})
                        except ValueError as error:
                            database.record_journal_failure(key,tab['title'].strip(),str(error),grade=book['grade'],
                                spreadsheet_title=book['spreadsheet_title'],sheet_title=tab['title'],sheet_id=tab['sheetId'])
                            outcomes.append({'status':'error','subject':tab['title'].strip(),'error':str(error)})
                        database.update_journal_sync_progress(run,{'total_tabs':len(tabs),'completed_tabs':len(outcomes),
                            'succeeded':sum(o['status']=='success' for o in outcomes),'failed':sum(o['status']=='error' for o in outcomes)})
                failed = [o for o in outcomes if o['status']=='error']
                counts = {'total_tabs':len(tabs),'completed_tabs':len(outcomes),'succeeded':len(outcomes)-len(failed),'failed':len(failed),
                    'validation_subset':int(repeat)}
                database.finish_journal_sync(run,status='partial' if failed else 'success',counts=counts)
                print(json.dumps({'grade':book['grade'],**counts,'issues':failed},ensure_ascii=False),flush=True)
            except Exception as error:
                database.finish_journal_sync(run,status='error',error=f'Rollout failed: {type(error).__name__}')
                raise
    if repeat:
        after = validation_state(database)
        changed = [key for key in before if before[key] != after[key]]
        print(json.dumps({'repeat_unchanged':not changed,'changed_categories':changed}),flush=True)
        if changed:
            raise RuntimeError('Repeat import changed stable journal data')


def capture(path: Path) -> None:
    client, account = _client(get_settings())
    if account != "antischool.island@gmail.com":
        raise RuntimeError("Unexpected Google account; refusing school source access")
    books = []
    for grade, spreadsheet_id in CURRENT_JOURNAL_SPREADSHEETS.items():
        metadata = client.spreadsheet(spreadsheet_id)
        tabs = [sheet["properties"] for sheet in metadata.get("sheets", [])]
        ranges = [f"'{tab['title'].replace(chr(39), chr(39)*2)}'!A1:{column_name(tab.get('gridProperties', {}).get('columnCount', 79)-1)}{tab.get('gridProperties', {}).get('rowCount', 1000)}" for tab in tabs]
        values = client.sheet_values_many(spreadsheet_id, ranges, value_render_option="UNFORMATTED_VALUE")
        books.append({"grade": grade, "spreadsheet_id": spreadsheet_id, "spreadsheet_title": metadata["properties"]["title"], "tabs": [{**tab, "values": value} for tab, value in zip(tabs, values, strict=True)]})
        print(json.dumps({"grade": grade, "tabs_captured": len(tabs)}, ensure_ascii=False), flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(books, ensure_ascii=False), encoding="utf-8")


def inspect(path: Path) -> None:
    for book in json.loads(path.read_text(encoding="utf-8")):
        for tab in book["tabs"]:
            try:
                parsed = parse_journal_values(tab["values"], grade=book["grade"], subject=tab["title"].strip(), sheet_title=tab["title"], sheet_id=tab["sheetId"])
                report = {"students": len(parsed["students"]), "works": len(parsed["assessments"]), "results": len(parsed["results"]), "summaries": len(parsed["summaries"]), "groups": sorted({s["group_marker"] for s in parsed["students"]})}
            except ValueError as error:
                report = {"error": str(error)}
            print(json.dumps({"grade": book["grade"], "subject": tab["title"].strip(), **report}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("capture", "inspect", "backup", "import", "repeat", "report"))
    parser.add_argument("--capture", type=Path, default=Path("data/school-journals-source.json"))
    parser.add_argument("--backup", type=Path, default=Path("data/school-journals-before-rollout.json.gz"))
    args = parser.parse_args()
    if args.operation == 'backup':
        backup(args.backup)
    elif args.operation == 'report':
        report()
    elif args.operation in {'import','repeat'}:
        import_capture(args.capture,args.backup,repeat=args.operation=='repeat')
    else:
        (capture if args.operation == "capture" else inspect)(args.capture)
