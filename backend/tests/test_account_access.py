from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch
import hashlib
import hmac
import json
import os
import secrets
import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.account_access import bootstrap_manager


TEST_TOKEN = "synthetic-access-integration-token"
WEBHOOK_SECRET = "synthetic-access-webhook-secret"
MANAGER = 90810001


def signed_headers(user_id, auth_date=None):
    values = {"auth_date": str(int(time.time()) if auth_date is None else auth_date),
              "user": json.dumps({"id": user_id, "first_name": "Example", "username": f"example_{user_id}"})}
    message = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    key = hmac.digest(b"WebAppData", TEST_TOKEN.encode(), "sha256")
    values["hash"] = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(values)}


class AccessFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "access.db"
        self.dsn = os.getenv("MY_ISLAND_ACCESS_TEST_DSN")
        if self.dsn:
            target = urlsplit(self.dsn)
            self.assertIn(target.hostname, {"127.0.0.1", "localhost", "::1"})
            self.assertEqual(target.path, "/my_island_access_test")
        self.db = Database(self.path, self.dsn)
        self.db.initialize()
        self.db.get_or_create_user(MANAGER, "admin")
        self.manager_id = bootstrap_manager(self.db, (MANAGER,))
        self.update_id = 10000 + secrets.randbelow(10**11)
        self.first_id = self.update_id * 10
        self.suffix = str(self.update_id).translate(str.maketrans("0123456789", "abcdefghij"))
        self.class_name = f"77 Test {self.update_id}"
        self.db.create_group(self.class_name, "class", base_class_name=self.class_name, actor_user_id=self.manager_id)
        cm = patch.dict(os.environ, {"APP_ENV": "development", "TELEGRAM_BOT_TOKEN": TEST_TOKEN,
                                     "TELEGRAM_WEBHOOK_SECRET": WEBHOOK_SECRET, "TELEGRAM_BOT_USERNAME": "synthetic_school_bot",
                                     "TELEGRAM_BOOTSTRAP_USER_IDS": str(MANAGER), "FRONTEND_PUBLIC_URL": "https://island.example"})
        cm.start()
        self.addCleanup(cm.stop)
        self.messages = []
        def capture(*args):
            self.messages.append(args)
            return False
        for target in ("backend.services.telegram_admin_bot._send_message", "backend.handlers.api.notify_registration_result"):
            cm = patch(target, side_effect=capture)
            cm.start()
            self.addCleanup(cm.stop)
        self.client = self.make_client()
        self.addCleanup(self.client.close)

    def make_client(self):
        app = FastAPI()
        app.include_router(create_router(self.db))
        return TestClient(app)

    def send(self, user_id, text, *, update_id=None, client=None):
        self.update_id += 1
        update = {"update_id": update_id or self.update_id, "message": {
            "from": {"id": user_id, "first_name": "Example", "username": f"example_{user_id}"},
            "chat": {"id": user_id, "type": "private"}, "text": text}}
        result = (client or self.client).post("/api/telegram/webhook", headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET}, json=update)
        self.assertEqual(result.status_code, 200, result.text)
        return update["update_id"]

    def registration(self, user_id, role, name, class_name=None):
        self.send(user_id, "/start")
        self.send(user_id, "Ученик" if role == "student" else "Преподаватель")
        if role == "student":
            self.send(user_id, "Другой класс")
            self.send(user_id, class_name)
        self.send(user_id, self.label(name))
        update_id = self.send(user_id, "Отправить заявку")
        self.send(user_id, "Отправить заявку", update_id=update_id)
        return self.pending(user_id)

    def pending(self, user_id):
        snapshot = self.client.get("/api/admin/access", headers=signed_headers(MANAGER)).json()
        account = next(item for item in snapshot["accounts"] if item["telegram_user_id"] == str(user_id))
        claims = [item for item in snapshot["claims"] if item["user_id"] == account["id"] and item["status"] == "pending"]
        self.assertEqual(len(claims), 1)
        return account, claims[0]

    def campus(self, user_id, role, *, headers=None):
        return self.client.post("/api/auth/session", headers=headers or signed_headers(user_id), json={"role": role})

    def change(self, account, action, *, actor=MANAGER, **fields):
        return self.client.post(f"/api/admin/access/accounts/{account['id']}",
                                headers=signed_headers(actor), json={"action": action, **fields})

    def review(self, claim, decision, *, actor=MANAGER, client=None, **fields):
        return (client or self.client).post(f"/api/admin/access/claims/{claim['id']}",
                                           headers=signed_headers(actor), json={"decision": decision, **fields})

    def label(self, name):
        return f"{name} {self.suffix}"

    def person(self, role, name, class_name=None):
        return str(self.db.create_identity(role, self.label(name), class_name, actor_user_id=self.manager_id)["id"])

    def test_student_registration_restart_transfer_and_personal_data(self):
        first, replacement, outsider = (self.first_id + i for i in range(3))
        name = "Тестовый Ученик"
        identity = self.person("student", name, self.class_name)
        other_identity = self.person("student", "Другой Ученик", self.class_name)
        self.send(first, "/start")
        self.send(first, "Ученик")
        self.send(first, "Другой класс")
        self.send(first, self.class_name)
        self.db = Database(self.path, self.dsn)
        with self.make_client() as restarted:
            self.send(first, "/start", client=restarted)
            self.assertIn("черновик", self.messages[-1][2])
            self.send(first, self.label(name), client=restarted)
            event = self.send(first, "Отправить заявку", client=restarted)
            self.send(first, "Отправить заявку", update_id=event, client=restarted)
        account, claim = self.pending(first)
        self.assertIn(identity, claim["candidate_ids"])
        self.assertEqual(self.campus(first, "admin").json()["state"], "needs_identity")
        self.assertEqual(self.client.get("/api/student/grades", headers=signed_headers(first)).status_code, 403)
        self.assertEqual(self.client.get("/api/admin/access", headers=signed_headers(first)).status_code, 403)
        response = self.review(claim, "approve", identity_id=identity)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(any(message[1:3] == (first, "approved") for message in self.messages))
        self.assertEqual(self.review(claim, "approve", identity_id=identity).status_code, 409)
        self.assertEqual(self.campus(first, "student").json()["state"], "approved")
        profile = self.client.get("/api/student/profile", headers=signed_headers(first)).json()
        self.assertEqual(str(profile["user"]["identity_id"]), identity)
        self.assertEqual(self.client.get("/api/student/schedule?start_day=2026-11-20", headers=signed_headers(first)).status_code, 200)
        self.assertEqual(self.client.get("/api/student/profile", headers=signed_headers(outsider)).status_code, 403)

        grade_sql = "INSERT INTO official_grades(identity_id, subject, graded_on, value, source) VALUES (?, ?, '2026-11-20', '5', 'test')"
        with self.db.connection() as connection:
            self.db.execute(connection, grade_sql, (identity, "Own test subject"))
            self.db.execute(connection, grade_sql, (other_identity, "Other test subject"))
        old_headers = signed_headers(first, int(time.time()) - 1)
        grades = self.client.get("/api/student/grades", headers=old_headers).json()["items"]
        self.assertEqual({item["subject"] for item in grades}, {"Own test subject"})
        new_account, new_claim = self.registration(replacement, "student", name, self.class_name)
        self.assertEqual(self.review(new_claim, "approve", identity_id=identity).status_code, 409)
        self.assertEqual(self.review(new_claim, "approve", identity_id=identity, replace_user_id=account["id"]).status_code, 200)
        self.assertEqual(self.client.get("/api/student/profile", headers=old_headers).status_code, 403)
        self.assertEqual(self.campus(first, "student", headers=old_headers).json()["state"], "revoked")
        self.assertEqual(self.client.get("/api/student/grades", headers=signed_headers(replacement)).json()["items"][0]["subject"], "Own test subject")

        replacement_headers = signed_headers(replacement, int(time.time()) - 1)
        self.assertEqual(self.change(new_account, "revoke").status_code, 200)
        self.assertEqual(self.client.get("/api/student/profile", headers=replacement_headers).status_code, 403)
        self.assertEqual(self.change(new_account, "restore").status_code, 200)
        self.assertEqual(self.campus(replacement, "student", headers=replacement_headers).json()["state"], "reopen_telegram")
        fresh = signed_headers(replacement, int(time.time()) + 1)
        self.assertEqual(self.campus(replacement, "student", headers=fresh).json()["state"], "approved")

    def test_teacher_admin_code_roles_and_preview(self):
        teacher_tg, other_tg = self.first_id + 4, self.first_id + 5
        teacher_identity = self.person("teacher", "Тестовый Преподаватель")
        student_identity = self.person("student", "Тест Дляпросмотра", self.class_name)
        account, claim = self.registration(teacher_tg, "teacher", "Тестовый Преподаватель")
        self.assertEqual(self.review(claim, "approve", identity_id=student_identity).status_code, 409)
        self.assertEqual(self.review(claim, "approve", identity_id=teacher_identity).status_code, 200)
        self.assertEqual(self.campus(teacher_tg, "teacher").json()["user"]["roles"], ["teacher"])
        self.assertEqual(self.change(account, "grant_admin").status_code, 200)
        self.assertEqual(self.campus(teacher_tg, "admin").json()["user"]["role"], "admin")
        self.assertEqual(self.campus(teacher_tg, "teacher").json()["user"]["role"], "teacher")
        self.assertEqual(self.client.get("/api/teacher/profile", headers=signed_headers(teacher_tg)).status_code, 200)
        token_response = self.client.get("/api/admin/access", headers=signed_headers(teacher_tg))
        self.assertFalse(token_response.json()["can_manage_administrators"])
        manager = next(row for row in token_response.json()["accounts"] if row["telegram_user_id"] == str(MANAGER))
        target = self.db.get_or_create_user(other_tg, "student")
        self.assertEqual(self.change({"id": target["id"]}, "grant_admin", actor=teacher_tg).status_code, 403)
        self.assertEqual(self.change(manager, "revoke", actor=teacher_tg).status_code, 403)
        self.assertEqual(self.change(manager, "remove_admin").status_code, 409)
        self.assertEqual(self.change(manager, "revoke").status_code, 409)
        legacy = self.client.post(f"/api/admin/users/{target['id']}/roles", headers=signed_headers(teacher_tg), json={"roles": ["student", "admin"]})
        self.assertEqual(legacy.status_code, 403)

        preview = self.client.post("/api/admin/previews", headers=signed_headers(teacher_tg),
                                   json={"target_identity_id": student_identity, "role": "student"})
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_headers = {**signed_headers(teacher_tg), "X-Preview-Id": preview.json()["id"]}
        result = self.client.get("/api/student/profile", headers=preview_headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.client.get("/api/student/profile", headers={**signed_headers(other_tg), "X-Preview-Id": preview.json()["id"]}).status_code, 403)

        self.send(teacher_tg, "/admincode")
        code = self.messages[-1][2].splitlines()[1]
        with self.make_client() as browser:
            login = browser.post("/api/admin/auth/exchange", json={"code": code})
            self.assertEqual(login.status_code, 200, login.text)
            self.assertEqual(browser.post("/api/admin/auth/exchange", json={"code": code}).status_code, 401)
            self.send(teacher_tg, "/admincode")
            fresh_code = self.messages[-1][2].splitlines()[1]
            self.assertEqual(self.change(account, "remove_admin").status_code, 200)
            self.assertEqual(browser.get("/api/admin/auth/session").status_code, 401)
            self.assertEqual(browser.post("/api/admin/auth/exchange", json={"code": fresh_code}).status_code, 401)
        self.assertEqual(self.client.get("/api/student/profile", headers=preview_headers).status_code, 403)
        self.assertEqual(self.campus(teacher_tg, "teacher", headers=signed_headers(teacher_tg, int(time.time()) + 1)).json()["user"]["roles"], ["teacher"])
        self.assertEqual(bootstrap_manager(self.db, (teacher_tg,)), self.manager_id)

    def test_unknown_match_rejection_resubmission_and_concurrent_review(self):
        telegram_id = self.first_id + 8
        account, claim = self.registration(telegram_id, "teacher", "Имя Несовпадение")
        self.assertEqual(claim["candidate_ids"], [])
        self.assertEqual(self.review(claim, "reject").status_code, 400)
        self.assertEqual(self.review(claim, "reject", reason="Уточните школьное имя").status_code, 200)
        self.send(telegram_id, "/status")
        self.assertIn("Уточните школьное имя", self.messages[-1][2])
        self.send(telegram_id, "Подать заново")
        self.send(telegram_id, "Преподаватель")
        self.send(telegram_id, self.label("Тест Повторный"))
        self.send(telegram_id, "Отправить заявку")
        account, retry = self.pending(telegram_id)
        identity = self.person("teacher", "Тест Повторный")
        self.assertNotEqual(claim["id"], retry["id"])
        second_admin = self.first_id + 7
        self.db.get_or_create_user(second_admin, "admin")
        def decide(actor):
            with self.make_client() as client:
                return self.review(retry, "approve", actor=actor, identity_id=identity, client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(decide, (MANAGER, second_admin))), [200, 409])
        self.assertEqual(self.campus(telegram_id, "teacher").json()["state"], "approved")

    def test_bad_init_data_and_browser_login_throttling(self):
        bad = self.client.post("/api/auth/session", json={"init_data": "hash=%D1%8F", "role": "admin"})
        self.assertEqual(bad.status_code, 401)
        with self.db.connection() as connection:
            self.db.execute(connection, "DELETE FROM access_login_attempts WHERE client_key=?", (self.db._token_hash("testclient"),))
        with self.make_client() as browser, patch("backend.database.time.time", return_value=time.time()):
            codes = [browser.post("/api/admin/auth/exchange", json={"code": "invalid"}).status_code for _ in range(16)]
        self.assertIn(429, codes)
        self.assertNotIn(200, codes)
        with self.db.connection() as connection:
            self.db.execute(connection, "DELETE FROM access_login_attempts WHERE client_key=?", (self.db._token_hash("testclient"),))

    def test_direct_link_closes_pending_claim_and_stale_pointer_cannot_authorize(self):
        telegram_id = self.first_id + 9
        account, claim = self.registration(telegram_id, "teacher", "Тест Дляпривязки")
        identity = self.person("teacher", "Тест Дляпривязки")
        self.assertEqual(self.change(account, "link", identity_id=identity, expected_identity_id=None).status_code, 200)
        self.assertEqual(self.review(claim, "approve", identity_id=identity).status_code, 409)
        self.assertTrue(any(message[1:3] == (telegram_id, "approved") for message in self.messages))
        with self.db.connection() as connection:
            self.db.execute(connection, "UPDATE account_identity_links SET status='revoked' WHERE user_id=?", (account["id"],))
        self.assertEqual(self.client.get("/api/teacher/profile", headers=signed_headers(telegram_id)).status_code, 403)
