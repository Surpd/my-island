"""Targeted, reversible classification repair; never changes score values.

Requires a fresh private backup and fresh bounded Google CellData captures.
The original rows and every score remain in the backup, raw_source and history.
"""
import argparse
import gzip
import json
from pathlib import Path

from backend.scripts.journal_school_rollout import production_database
from backend.services.journal_import import _display_name, parse_journal_values


def repair(backup_path: Path, capture_paths: list[Path], apply: bool = False):
    with gzip.open(backup_path,'rt',encoding='utf-8') as handle:
        if json.load(handle)['project_id'] != 'kmrcbewmxeqqxfauiwyy':
            raise RuntimeError('Backup target mismatch')
    captures = [json.loads(path.read_text(encoding='utf-8')) for path in capture_paths]
    database = production_database()
    counts = {'auxiliary_rows':0,'team_rows':0,'private_names_sanitized':0,'excluded_statistics':0}
    with database.journal_workbook_lock('__school_import__') as acquired:
        if not acquired:
            raise RuntimeError('Another journal import is active')
        with database.connection() as connection:
            if database.execute(connection,"SELECT id FROM journal_sync_runs WHERE status='running' LIMIT 1").fetchone():
                raise RuntimeError('Wait for queued imports to finish')
            previous = {'students':[],'results':[],'sources':[]}
            for book in captures:
                grade = 10 if book['spreadsheetId'] == '1fkljnYY4WHNRVkDeQHIqFeKGLAE4kqh-TT33Wb8TRgk' else 11
                if book['spreadsheetId'] not in {'1fkljnYY4WHNRVkDeQHIqFeKGLAE4kqh-TT33Wb8TRgk','1q4zVwRwQObfgvpdg2WntKfdFAnDSW8hn7-2Ar_uutco'}:
                    raise RuntimeError('Capture target mismatch')
                for tab in book['sheets']:
                    props = tab['properties']
                    values = []
                    for block in tab.get('data',[]):
                        if block.get('startRow',0) or block.get('startColumn',0):
                            raise RuntimeError('Capture must begin at A1')
                        for row in block.get('rowData',[]):
                            values.append([next(iter(cell.get('effectiveValue',{}).values()),None) for cell in row.get('values',[])])
                    parsed = parse_journal_values(values,grade=grade,subject=props['title'].strip(),sheet_title=props['title'],sheet_id=props['sheetId'])
                    valid_rows = {item['source_row'] for item in parsed['students']}
                    team_rows = {item['source_row'] for item in parsed['students'] if item.get('entry_kind') == 'team'}
                    rows = database.execute(connection,"""SELECT st.* FROM journal_students st JOIN journal_sources src ON src.id=st.source_id
                        WHERE src.spreadsheet_id=? AND src.sheet_id=?""", (book['spreadsheetId'],str(props['sheetId']))).fetchall()
                    for record in rows:
                        row = dict(record)
                        auxiliary = grade == 11 and props['sheetId'] == 1540004937 and isinstance(row['source_row'],int) and row['source_row'] >= 27 and row['source_row'] not in valid_rows
                        team = row['source_row'] in team_rows
                        if not auxiliary and not (team and row['entry_kind'] != 'team'):
                            continue
                        captured_name = str(values[row['source_row']-1][0] or '').strip() if row['source_row'] and row['source_row'] <= len(values) else ''
                        if _display_name(captured_name) != _display_name(row['display_name']):
                            raise RuntimeError('Source rows changed since capture; refresh the affected capture before repair')
                        previous['students'].append(row)
                        results = [dict(r) for r in database.execute(connection,'SELECT * FROM journal_results WHERE journal_student_id=?',(row['id'],)).fetchall()]
                        previous['results'].extend(results)
                        counts['auxiliary_rows' if auxiliary else 'team_rows'] += 1
                        if not apply:
                            continue
                        if auxiliary:
                            classification = 'auxiliary_table'
                            if row['source_row'] == 28:
                                classification = 'auxiliary_header'
                            elif row['source_row'] == 39:
                                classification = 'auxiliary_statistics'
                            database.execute(connection,"UPDATE journal_students SET active=FALSE,classification_method=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(classification,row['id']))
                            for result in results:
                                if result['is_current']:
                                    database.execute(connection,"""INSERT INTO journal_result_history(result_id,numeric_score,status,source_coordinate,raw_source,is_current)
                                        VALUES (?,?,?,?,?::jsonb,TRUE)""",(result['id'],result['numeric_score'],result['status'],result['source_coordinate'],json.dumps(result['raw_source'],ensure_ascii=False,default=str)))
                            database.execute(connection,'UPDATE journal_results SET is_current=FALSE WHERE journal_student_id=?',(row['id'],))
                            database.execute(connection,'UPDATE journal_student_summaries SET is_current=FALSE WHERE journal_student_id=?',(row['id'],))
                        else:
                            database.execute(connection,"UPDATE journal_students SET entry_kind='team',classification_method='paired_names_in_art_journal',identity_id=NULL,match_status='unlinked',match_method=NULL WHERE id=?",(row['id'],))
                            database.execute(connection,'UPDATE journal_results SET identity_id=NULL WHERE journal_student_id=?',(row['id'],))
            for record in database.execute(connection,'SELECT id,display_name,raw_source FROM journal_students').fetchall():
                clean = _display_name(record['display_name'])
                if clean and clean != record['display_name']:
                    counts['private_names_sanitized'] += 1
                    previous['students'].append(dict(record))
                    if apply:
                        database.execute(connection,'UPDATE journal_students SET display_name=? WHERE id=?',(clean,record['id']))
                        database.execute(connection,'UPDATE journal_results SET student_name=? WHERE journal_student_id=?',(clean,record['id']))
            stats = database.execute(connection,"SELECT * FROM journal_sources WHERE spreadsheet_id=? AND sheet_id='458194089'",('1q4zVwRwQObfgvpdg2WntKfdFAnDSW8hn7-2Ar_uutco',)).fetchone()
            if stats and not stats['excluded_from_journals']:
                counts['excluded_statistics'] = 1
                previous['sources'].append(dict(stats))
                if apply:
                    database.execute(connection,"UPDATE journal_sources SET excluded_from_journals=TRUE,auto_sync_enabled=FALSE,last_error=NULL,status='unsupported' WHERE id=?",(stats['id'],))
            if apply and any(counts.values()):
                database.execute(connection,"INSERT INTO audit_log(action,entity_type,details) VALUES ('journal_quality.reclassified','journal_source',?)",(json.dumps({'counts':counts,'previous':previous,'backup':backup_path.name},ensure_ascii=False,default=str),))
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--capture',type=Path,action='append',required=True)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    print(json.dumps(repair(args.backup,args.capture,args.apply)))
