"""Start the real My Island API against a disposable, synthetic-only SQLite DB.

Run from the repository root: python scripts/schedule-regression/local_harness.py
The process binds only 127.0.0.1:8041 and blocks outbound network sockets.
"""
from __future__ import annotations

import ipaddress
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys


ROOT = Path(__file__).resolve().parents[2]
DATA = (ROOT / ".local-test" / "consolidation").resolve()
DB_PATH = (DATA / "my-island-test.sqlite3").resolve()
AUTH_PATH = DATA / "auth.json"
FIXTURE_PATH = DATA / "fixture.json"
ARTIFACTS = (ROOT / ".local-test" / "consolidation" / "results").resolve()
EXPECTED_PARENT = (ROOT / ".local-test" / "consolidation").resolve()
if DB_PATH.parent != EXPECTED_PARENT or not DB_PATH.is_relative_to(ROOT):
    raise RuntimeError("Refusing database path outside .local-test/consolidation")
if not ROOT.is_dir():
    raise RuntimeError("Repository root is unavailable")
DATA.mkdir(parents=True, exist_ok=True)
ARTIFACTS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

# This child process intentionally ignores repository and user dotenv files.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for key in list(os.environ):
    if key.startswith(("GOOGLE_", "TELEGRAM_", "SUPABASE_", "RENDER_")) or key in {
        "DATABASE_URL", "DATABASE_PATH", "MY_ISLAND_SCHOOL_WORKSPACE",
    }:
        os.environ.pop(key, None)
os.environ.update({
    "APP_ENV": "development",
    "DEV_AUTH_ENABLED": "true",
    "DATABASE_URL": "",
    "DATABASE_PATH": str(DB_PATH),
    "MY_ISLAND_LOCAL_TEST_MODE": "true",
    "MY_ISLAND_TEST_PROFILE": "consolidation",
    "MY_ISLAND_CONSOLIDATED_SCHEDULE": "1",
    "MY_ISLAND_TEST_API_PORT": "8041",
    "SCHEDULE_BACKEND": "canonical",
    "CORS_ORIGINS": "http://localhost:3041,http://127.0.0.1:3041",
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_WEBHOOK_SECRET": "", "TELEGRAM_BOOTSTRAP_USER_IDS": "",
    "GOOGLE_SHEETS_SPREADSHEET_ID": "", "GOOGLE_CLASSROOM_COURSE_ID": "",
    "GOOGLE_OAUTH_CLIENT_ID": "", "GOOGLE_OAUTH_CLIENT_SECRET": "",
    "GOOGLE_OAUTH_REDIRECT_URI": "", "GOOGLE_OAUTH_REFRESH_TOKEN": "",
    "GOOGLE_OAUTH_TOKEN_ENCRYPTION_KEY": "", "BACKEND_PUBLIC_URL": "", "FRONTEND_PUBLIC_URL": "",
})


def _loopback(address: object) -> bool:
    host = address[0] if isinstance(address, tuple) and address else ""
    if str(host).casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


_connect, _connect_ex, _sendto = socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto


def _guard_connect(self: socket.socket, address: object):
    if not _loopback(address):
        raise OSError("Consolidation fixture egress blocked: localhost only")
    return _connect(self, address)


def _guard_connect_ex(self: socket.socket, address: object):
    if not _loopback(address):
        return 10013
    return _connect_ex(self, address)


def _guard_sendto(self: socket.socket, data: bytes, *args: object):
    address = args[-1] if len(args) >= 2 else args[0]
    if not _loopback(address):
        raise OSError("Consolidation fixture egress blocked: localhost only")
    return _sendto(self, data, *args)


socket.socket.connect = _guard_connect
socket.socket.connect_ex = _guard_connect_ex
socket.socket.sendto = _guard_sendto


def main() -> None:
    reuse = "--reuse" in sys.argv[1:]
    if reuse and (not DB_PATH.is_file() or not FIXTURE_PATH.is_file()):
        raise RuntimeError("Cannot reuse consolidation profile: expected fixture DB and manifest are missing")
    # Default runs rebuild only this exact disposable DB. --reuse is for
    # restarting the isolated API after source edits without discarding local
    # publication/color state.
    if not reuse and DB_PATH.exists():
        DB_PATH.unlink()
    from backend.database import Database
    spec = importlib.util.spec_from_file_location("consolidation_fixture", Path(__file__).with_name("fixture.py"))
    if not spec or not spec.loader:
        raise RuntimeError("Could not load synthetic fixture generator")
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)

    database = Database(path=DB_PATH)
    database.initialize()
    if reuse:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        if not fixture.get("synthetic"):
            raise RuntimeError("Refusing to reuse a non-synthetic fixture manifest")
    else:
        fixture = fixture_module.seed(database)

    # Link the app's three loopback-only dev principals to synthetic fixture
    # identities so student/teacher read paths exercise real runtime projection.
    student_identity = fixture["students"]["7-А synthetic"][0]
    teacher_identity = fixture["teachers"][0]
    for telegram_id, role, identity_id in (
        (900001, "student", student_identity),
        (900003, "teacher", teacher_identity),
        (900002, "admin", None),
    ):
        user = database.get_or_create_user(telegram_id, role)
        if identity_id:
            with database.connection() as c:
                c.execute("UPDATE users SET identity_id=? WHERE id=?", (identity_id, user["id"]))
        with database.connection() as c:
            c.execute("INSERT OR IGNORE INTO user_roles(user_id,role,source) VALUES (?,?,?)", (user["id"], role, "local-consolidation"))
    admin = database.get_or_create_user(900002, "admin")
    challenge, _ = database.create_admin_login_challenge(admin["id"])
    session = database.consume_admin_login_challenge(challenge)
    if not session:
        raise RuntimeError("Could not issue isolated admin browser session")
    session_token = session[0]
    challenge2, _ = database.create_admin_login_challenge(admin["id"])
    session2 = database.consume_admin_login_challenge(challenge2)
    if not session2:
        raise RuntimeError("Could not issue second isolated admin browser session")
    fixture["runtime_auth"] = {"student_header": "student:1", "teacher_header": "teacher:1", "admin_header": "admin:1"}
    FIXTURE_PATH.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    AUTH_PATH.write_text(json.dumps({
        "base_url": "http://127.0.0.1:8041",
        "start_command": "python scripts/schedule-regression/local_harness.py --reuse" if reuse
            else "python scripts/schedule-regression/local_harness.py",
        "admin_cookie": "my_island_admin_session=" + session_token,
        "admin_browser_session": session_token,
        "device_b_admin_browser_session": session2[0],
        "dev_auth": {"student": "student:1", "teacher": "teacher:1", "admin": "admin:1"},
        "database": str(DB_PATH),
        "synthetic": True,
    }, indent=2), encoding="utf-8")

    if "--seed-only" in sys.argv[1:]:
        print(f"Synthetic fixture seeded: {fixture['counts']}; canonical={fixture['canonical_version_id']}; DB is local-only.", flush=True)
        return

    import uvicorn
    from backend.main import app
    print("Synthetic-only consolidation API ready at http://127.0.0.1:8041; auth tokens are in .local-test/consolidation/auth.json", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8041, reload=False, log_level="info")


if __name__ == "__main__":
    main()
