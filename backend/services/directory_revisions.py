"""Transactional optimistic editing of school directory aggregates."""
from contextlib import contextmanager, nullcontext
from copy import copy
import hashlib
import json

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder


# These are the fields exposed in the administrative editors. A membership or
# assignment belongs to both the person and the group in which it appears.
ROOT_FIELDS = {
    "identities": ("display_name", "class_name", "status", "admin_note", "kind"),
    "groups": ("name", "display_name", "group_type", "subject", "base_class_name",
               "subject_subgroup", "exam_track", "archived_at", "canonical", "admin_note"),
}
RELATIONS = {
    "memberships": ("identity_id", "memberships"),
    "membership_overrides": ("identity_id", "memberships"),
    "teacher_assignments": ("teacher_identity_id", "assignments"),
    "homeroom_assignments": ("teacher_identity_id", "homeroom_assignments"),
    "teacher_subjects": ("teacher_identity_id", "subjects"),
}


def initialize_history(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS directory_edit_history (
        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL,
        changed_fields TEXT NOT NULL, actor_id TEXT, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(entity_type,entity_id,revision))""")
    if "field_values" not in {row[1] for row in connection.execute("PRAGMA table_info(directory_edit_history)")}:
        connection.execute("ALTER TABLE directory_edit_history ADD COLUMN field_values TEXT NOT NULL DEFAULT '{}'")


def initialize_sqlite(connection):
    initialize_history(connection)
    # SQLite serializes writers. The actor exists only inside the editing
    # transaction, including when a nested membership/assignment write occurs.
    connection.execute("CREATE TABLE IF NOT EXISTS directory_edit_actor (id INTEGER PRIMARY KEY CHECK(id=1), actor_id TEXT)")
    for table, fields in ROOT_FIELDS.items():
        if "revision" not in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        condition = " OR ".join(f"NEW.{f} IS NOT OLD.{f}" for f in fields)
        changed = " UNION ALL ".join(f"SELECT '{f}' AS field WHERE NEW.{f} IS NOT OLD.{f}" for f in fields)
        safe = [f for f in fields if f in {"kind", "status", "group_type", "canonical"}]
        deltas = " UNION ALL ".join(
            f"SELECT '{f}' AS field,json_object('before',OLD.{f},'after',NEW.{f}) AS delta WHERE NEW.{f} IS NOT OLD.{f}"
            for f in safe)
        values = f"(SELECT json_group_object(field,json(delta)) FROM ({deltas}))"
        connection.execute(f"DROP TRIGGER IF EXISTS directory_{table}_revision")
        bump = _sqlite_bump(table, "id=NEW.id", f"(SELECT json_group_array(field) FROM ({changed}))", values)
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS directory_{table}_revision AFTER UPDATE OF {','.join(fields)}
            ON {table} WHEN {condition} BEGIN {bump} END""")

    for table, (owner, field) in RELATIONS.items():
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                   if row[1] not in {"id", "created_at", "created_by", "reason"}]
        for event, refs in (("INSERT", ("NEW",)), ("DELETE", ("OLD",)), ("UPDATE", ("NEW", "OLD"))):
            condition = (" WHEN " + " OR ".join(f"OLD.{col} IS NOT NEW.{col}" for col in columns)
                         if event == "UPDATE" else "")
            owners = [("identities", owner)]
            if table != "teacher_subjects":
                owners.append(("groups", "class_group_id" if table == "homeroom_assignments" else "group_id"))
            values = "'{}'"
            if "active" in columns:
                before = "OLD.active" if event != "INSERT" else "NULL"
                after = "NEW.active" if event != "DELETE" else "NULL"
                values = f"json_object('active',json_object('before',{before},'after',{after}))"
            connection.execute(f"DROP TRIGGER IF EXISTS directory_{table}_{event}")
            body = ""
            for root, foreign_key in owners:
                keys = ",".join(f"{ref}.{foreign_key}" for ref in refs)
                body += _sqlite_bump(root, f"id IN ({keys})", f"json_array('{field}')", values)
            connection.execute(f"CREATE TRIGGER IF NOT EXISTS directory_{table}_{event} AFTER {event} ON {table}{condition} BEGIN {body} END")


def _sqlite_bump(table, predicate, fields, values):
    return f"""UPDATE {table} SET revision=revision+1 WHERE {predicate};
        INSERT INTO directory_edit_history(entity_type,entity_id,revision,changed_fields,field_values,actor_id)
        SELECT '{table}',CAST(id AS TEXT),revision,{fields},{values},(SELECT actor_id FROM directory_edit_actor WHERE id=1)
        FROM {table} WHERE {predicate};"""


@contextmanager
def directory_transaction(database, actor_id):
    """Existing directory services share one connection and one commit."""
    with database.connection() as connection:
        transaction = connection.transaction() if database.database_url else nullcontext()
        if not database.database_url:
            connection.execute("BEGIN IMMEDIATE")
        with transaction:
            if database.database_url:
                database.execute(connection, "SELECT set_config('my_island.directory_actor', ?, true)", (str(actor_id),))
                # Opposite edits to a group and a teacher may take their two
                # parent locks in opposite order. The aborted writer can reload.
            else:
                connection.execute("INSERT INTO directory_edit_actor VALUES (1,?)", (str(actor_id),))
            bound = copy(database)
            bound.connection = lambda: nullcontext(connection)
            try:
                yield bound
            except Exception as error:
                if getattr(error, "sqlstate", None) in {"40P01", "40001"}:
                    raise HTTPException(409, "Одновременное изменение. Обновите запись и повторите сохранение.") from error
                raise
            if not database.database_url:
                connection.execute("DELETE FROM directory_edit_actor WHERE id=1")


def check_revision(database, table, entity_id, expected_revision):
    if table not in ROOT_FIELDS:
        raise ValueError("Unknown directory entity")
    if type(expected_revision) is not int or expected_revision < 1:
        raise HTTPException(428, "Обновите справочник и откройте форму заново: требуется версия записи.")
    lock = " FOR UPDATE" if database.database_url else ""
    with database.connection() as connection:
        current = database.execute(connection, f"SELECT * FROM {table} WHERE id=?{lock}", (entity_id,)).fetchone()
    if current is None:
        raise HTTPException(404, "Запись справочника удалена.")
    if current["revision"] != expected_revision:
        raise HTTPException(409, {"code": "directory_revision_conflict", "current": jsonable_encoder(dict(current)),
            "message": "Запись уже изменена. Обновите справочник и сравните изменения."})


@contextmanager
def directory_edit(database, table, entity_id, expected_revision, actor_id):
    with directory_transaction(database, actor_id) as bound:
        check_revision(bound, table, entity_id, expected_revision)
        yield bound


def add_group_members(database, group_id, members, actor):
    if not isinstance(members, list):
        raise ValueError("add_members must be a list")
    expected = {}
    for item in members:
        if not isinstance(item, dict) or not isinstance(item.get("identity_id"), (str, int)):
            raise ValueError("Invalid member")
        identity_id = str(item["identity_id"])
        if identity_id in expected:
            raise ValueError("Duplicate member")
        expected[identity_id] = item.get("expected_revision")
    for identity_id in sorted(expected):
        check_revision(database, "identities", identity_id, expected[identity_id])
        database.update_student_memberships(identity_id, None, group_id, actor, "Added in group editor")


def directory_history(database, table, entity_id):
    if table not in ROOT_FIELDS:
        raise HTTPException(404, "Unknown directory entity")
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT revision,actor_id,changed_at,changed_fields,field_values
            FROM directory_edit_history WHERE entity_type=? AND entity_id=?
            ORDER BY revision DESC LIMIT 100""", (table, str(entity_id))).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        for key in ("changed_fields", "field_values"):
            if isinstance(item[key], str):
                item[key] = json.loads(item[key])
    return {"items": items}


def directory_version(database):
    """A token for exactly the directory rows, including inserts and removals."""
    digest = hashlib.sha256()
    with database.connection() as connection:
        rows = database.execute(connection, """SELECT 'g' AS kind, CAST(id AS TEXT) AS key, revision FROM groups
            UNION ALL SELECT 'i', CAST(id AS TEXT), revision FROM identities ORDER BY 1,2""")
        for row in rows:
            digest.update(f'{row["kind"]}/{row["key"]}/{row["revision"]}\n'.encode())
    return digest.hexdigest()
