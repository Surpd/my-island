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
                for url, body in (self.publication, self.audience)]

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
            self.assertEqual(self.statuses(headers), [200, 200])
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
            self.assertEqual(self.statuses(headers), [status, status])

    def test_ordinary_credentials_do_not_enable_mutation_routes(self):
        self.arrange()
        for headers in (self.telegram(760000), {"X-Dev-Auth": f"browser:{self.token}"}):
            publication = self.client.post(f"{self.prefix}/publication/publish", headers=headers,
                json={**self.publication[1], "preview_token": "synthetic", "confirmed": True})
            rosters = self.client.post(f"{self.prefix}/audiences/apply", headers=headers,
                json={**self.audience[1], "preview_token": "synthetic", "reason": "synthetic"})
            self.assertEqual((publication.status_code, rosters.status_code), (401, 401))
