"""Verify ordinary human auth at exact constructor preview routes only."""
import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.handlers.constructor_publication_api import create_constructor_publication_router
from backend.services.consolidated_workspace import require_consolidated_admin, require_consolidated_preview_admin
from backend.tests import test_constructor_publication as fixtures


class ConstructorPreviewHumanAuthTests(TestCase):
    setUp = fixtures.ConstructorPublicationTests.setUp
    tearDown = fixtures.ConstructorPublicationTests.tearDown
    lesson = fixtures.ConstructorPublicationTests.lesson
    draft = fixtures.ConstructorPublicationTests.draft

    def arrange(self):
        self.bot_secret = "synthetic-constructor-bot-credential"
        settings = SimpleNamespace(app_env="development", local_test_mode=True)
        patches = [
            patch.dict(os.environ, {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1", "MY_ISLAND_TEST_PROFILE": "consolidation"}),
            patch("backend.services.consolidated_workspace.get_settings", return_value=settings),
            patch("backend.services.auth.Settings.from_env", return_value=SimpleNamespace(
                app_env="production", dev_auth_enabled=False, telegram_bot_token=self.bot_secret)),
        ]
        for change in patches:
            change.start()
            self.addCleanup(change.stop)
        for i, role in enumerate(("admin", "teacher", "student"), start=760000):
            user = self.database.get_or_create_user(i, role)
            if role == "admin":
                challenge, _ = self.database.create_admin_login_challenge(user["id"])
                self.token, _ = self.database.consume_admin_login_challenge(challenge)
                stale, _ = self.database.create_admin_login_challenge(user["id"])
                self.stale_token, _ = self.database.consume_admin_login_challenge(stale)
                with self.database.connection() as connection:
                    connection.execute("UPDATE admin_browser_sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=(SELECT max(id) FROM admin_browser_sessions)")
        app = FastAPI()
        app.include_router(create_constructor_publication_router(
            lambda: (self.database, self.database),
            lambda token: require_consolidated_admin(self.database, token),
            lambda token, request: require_consolidated_preview_admin(self.database, token, request)))
        self.client = TestClient(app)
        record = self.draft()
        prefix = f"/api/admin/schedule/constructor/{record['id']}"
        self.publication = (f"{prefix}/publication/preview", {"expected_revision": 1, "scope_kind": "template"})
        self.audience = (f"{prefix}/audiences/preview", {"expected_revision": 1, "scope": "template"})
        self.resolution = (f"{prefix}/resolution/preview", {"expected_revision": 1, "lesson_ids": ["l1"]})
        self.prefix = prefix

    def telegram(self, external_user_id, *, age=0, secret=None):
        fields = {"auth_date": str(int(time.time()) - age),
                  "user": json.dumps({"id": external_user_id, "role": "admin"}, separators=(",", ":"))}
        token = hmac.new(b"WebAppData", (secret or self.bot_secret).encode(), hashlib.sha256).digest()
        message = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        fields["hash"] = hmac.new(token, message.encode(), hashlib.sha256).hexdigest()
        return {"X-Telegram-Init-Data": urlencode(fields)}

    def statuses(self, headers):
        return [self.client.post(url, json=body, headers=headers).status_code
                for url, body in (self.publication, self.audience, self.resolution)]

    def test_read_only_preview_supports_both_validated_human_transports(self):
        self.arrange()
        with self.database.connection() as connection:
            before = tuple(connection.iterdump())
        methods = [
            self.telegram(760000),
            {"X-Dev-Auth": f"browser:{self.token}"},
            {"X-School-Admin-Session": self.token},
            {**self.telegram(760000), "X-School-Admin-Session": self.stale_token},
        ]
        for headers in methods:
            self.assertEqual(self.statuses(headers), [200, 200, 200])
        with self.database.connection() as connection:
            self.assertEqual(tuple(connection.iterdump()), before)

    def test_preview_checks_database_role_signature_expiry_and_refuses_diagnostics(self):
        self.arrange()
        for headers, status in [
            ({}, 401), ({"X-Dev-Auth": "admin:1", "X-Admin-Role": "admin"}, 401),
            ({"X-Dev-Auth": f"browser:{self.stale_token}"}, 401),
            ({"X-School-Admin-Session": "not-a-real-session"}, 401),
            (self.telegram(760000, age=90000), 401),
            (self.telegram(760000, secret="wrong synthetic credential"), 401),
            (self.telegram(760001), 403), (self.telegram(760002), 403),
            ({**self.telegram(760000), "Authorization": "Bearer diagnostic-synthetic"}, 403),
            ({**self.telegram(760000), "X-Preview-Id": "synthetic-preview"}, 403),
            ({**self.telegram(760000), "X-Student-Preview-Id": "synthetic-preview"}, 403),
        ]:
            self.assertEqual(self.statuses(headers), [status, status, status])

    def test_resolution_apply_accepts_only_authenticated_admin_and_keeps_no_teacher_practice_explicit(self):
        self.arrange()
        admin = self.database.find_user(760000)
        for headers in (self.telegram(760000), {"X-Dev-Auth": f"browser:{self.token}"}):
            record = self.draft([self.lesson(id="practice-no-teacher", teacherId="", teacherIds=[])])
            base = f"/api/admin/schedule/constructor/{record['id']}/resolution/"
            body = {"expected_revision": record["revision"], "lesson_ids": ["practice-no-teacher"]}
            preview = self.client.post(base + "preview", json=body, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            applied = self.client.post(base + "apply", headers=headers, json={
                **body, "preview_token": preview.json()["preview_token"],
                "action": "independent_practice", "reason": "Проверено администратором",
            })
            self.assertEqual(applied.status_code, 200, applied.text)
            lesson = applied.json()["payload"]["lessons"][0]
            self.assertFalse(lesson["teacherId"])
            self.assertEqual(lesson["resolution_history"][0]["type"], "independent_practice")
            self.assertEqual(lesson["resolution_history"][0]["actor_id"], str(admin["id"]))

        rejected = self.draft([self.lesson(id="rejected", teacherId="", teacherIds=[])])
        base = f"/api/admin/schedule/constructor/{rejected['id']}/resolution/"
        body = {"expected_revision": rejected["revision"], "lesson_ids": ["rejected"]}
        apply_body = {**body, "preview_token": "not-authorized", "action": "independent_practice", "reason": "Нет доступа"}
        for headers, status in [({}, 401), (self.telegram(760001), 403), (self.telegram(760002), 403)]:
            self.assertEqual(self.client.post(base + "preview", json=body, headers=headers).status_code, status)
            self.assertEqual(self.client.post(base + "apply", json=apply_body, headers=headers).status_code, status)

    def test_single_lesson_roster_apply_accepts_human_admin_without_enabling_bulk_apply(self):
        self.arrange()
        admin = self.database.find_user(760000)
        for headers in (self.telegram(760000), {"X-Dev-Auth": f"browser:{self.token}"}):
            record = self.draft()
            base = f"/api/admin/schedule/constructor/{record['id']}/audiences/"
            body = {"expected_revision": record["revision"], "scope": "lesson", "lesson_id": "l1"}
            preview = self.client.post(base + "preview", json=body, headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            applied = self.client.post(base + "apply", headers=headers, json={
                **body, "preview_token": preview.json()["preview_token"],
                "reason": "Проверен состав одного занятия",
            })
            self.assertEqual(applied.status_code, 200, applied.text)
            history = applied.json()["payload"]["lessons"][0]["resolution_history"]
            self.assertEqual(history[-1]["actor_id"], str(admin["id"]))

            bulk = self.client.post(base + "apply", headers=headers, json={
                "expected_revision": applied.json()["revision"], "scope": "template",
                "preview_token": "not-authorized", "reason": "Не должно пройти",
            })
            self.assertEqual(bulk.status_code, 401, bulk.text)

        rejected = self.draft()
        base = f"/api/admin/schedule/constructor/{rejected['id']}/audiences/"
        body = {"expected_revision": rejected["revision"], "scope": "lesson", "lesson_id": "l1"}
        preview = self.client.post(base + "preview", json=body)
        self.assertEqual(preview.status_code, 401, preview.text)
        apply = self.client.post(base + "apply", json={
            **body, "preview_token": "not-authorized", "reason": "Нет доступа",
        })
        self.assertEqual(apply.status_code, 401, apply.text)

    def test_ordinary_credentials_do_not_enable_mutation_routes(self):
        self.arrange()
        for headers in (self.telegram(760000), {"X-Dev-Auth": f"browser:{self.token}"}):
            publication = self.client.post(f"{self.prefix}/publication/publish", headers=headers,
                json={**self.publication[1], "preview_token": "synthetic", "confirmed": True})
            rosters = self.client.post(f"{self.prefix}/audiences/apply", headers=headers,
                json={**self.audience[1], "preview_token": "synthetic", "reason": "synthetic"})
            self.assertEqual((publication.status_code, rosters.status_code), (401, 401))
