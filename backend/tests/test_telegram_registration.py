import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.database import Database
from backend.handlers.api import create_router
from backend.services.telegram_admin_bot import handle_telegram_update, notify_registration_result


class TelegramRegistrationTests(unittest.TestCase):
    telegram_id = 800123
    web_app_url = "https://island.example/"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "registration.db"
        self.database = Database(self.path)
        self.database.initialize()
        self.messages = []
        self.update_id = 0

    def payload(self, message, *, chat_type="private", sender_id=None):
        self.update_id += 1
        return {
            "update_id": self.update_id,
            "message": {
                "chat": {"type": chat_type, "id": self.telegram_id},
                "from": {"id": self.telegram_id if sender_id is None else sender_id,
                         "username": "synthetic", "first_name": "Тест", "last_name": "Аккаунт"},
                "text": message,
            },
        }

    def deliver(self, update):
        return handle_telegram_update(
            self.database, update, "synthetic-token", self.web_app_url,
            lambda token, chat, text, markup: self.messages.append((chat, text, markup)),
        )

    def send(self, message):
        self.assertTrue(self.deliver(self.payload(message)))
        return self.messages[-1][1:]

    def claims(self):
        with self.database.connection() as connection:
            return [dict(row) for row in self.database.execute(connection, """
                SELECT * FROM identity_claims ORDER BY id
            """).fetchall()]

    def test_student_draft_survives_restart_and_delivery_is_idempotent(self):
        self.database.create_identity("student", "Скрытое Имя", "8Б")
        self.send("/start@school_bot")
        _, keyboard = self.send("Ученик")
        self.assertIn("8Б", [button["text"] for row in keyboard["keyboard"] for button in row])
        self.assertNotIn("Скрытое Имя", str(keyboard))
        self.send("8Б")
        self.send("Синтетический Ученик")
        self.database = Database(self.path)
        summary, _ = self.send("/start")
        self.assertIn("Синтетический Ученик", summary)
        self.assertIn("8Б", summary)
        self.send("Исправить имя")
        self.send("Пример Ученик")
        submit = self.payload("Отправить заявку")
        self.assertTrue(self.deliver(submit))
        reply_count = len(self.messages)
        self.assertTrue(self.deliver(submit))
        self.assertEqual(len(self.messages), reply_count)
        status, _ = self.send("/status")
        self.assertIn("ждёт проверки", status)
        claim, = self.claims()
        self.assertEqual(claim["submitted_name"], "Пример Ученик")
        self.assertEqual(claim["submitted_class_name"], "8Б")
        self.assertIsNone(claim["identity_id"])
        user = dict(self.database.find_user(self.telegram_id))
        self.assertEqual(user["telegram_username"], "synthetic")
        self.assertEqual(user["telegram_first_name"], "Тест")
        self.assertEqual(user["telegram_last_name"], "Аккаунт")

    def test_teacher_can_cancel_and_resubmit_after_rejection(self):
        self.send("/start")
        self.send("Преподаватель")
        self.send("/cancel")
        self.assertIn("пока нет", self.send("/status")[0])
        self.send("/start")
        self.send("Преподаватель")
        self.send("Пример Учитель")
        self.send("Отправить заявку")
        with self.database.connection() as connection:
            self.database.execute(connection,
                                  "UPDATE identity_claims SET status = 'rejected', review_reason = 'Уточните фамилию'")
        self.assertIn("Уточните фамилию", self.send("/status")[0])
        self.send("Подать заново")
        self.send("Преподаватель")
        self.send("Другой Учитель")
        self.send("Отправить заявку")
        rejected, pending = self.claims()
        self.assertEqual((rejected["status"], pending["status"]), ("rejected", "pending"))
        self.assertIsNone(pending["submitted_class_name"])

    def test_confirmed_person_opens_island_and_revocation_removes_menu(self):
        self.send("/start")
        identity = self.database.create_identity("teacher", "Учитель Пример")
        user = self.database.find_user(self.telegram_id)
        with self.database.connection() as connection:
            self.database.execute(connection, """
                INSERT INTO account_identity_links(user_id, identity_id, status, source)
                VALUES (?, ?, 'confirmed', 'test')
            """, (user["id"], identity["id"]))
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'teacher')", (user["id"],))
            self.database.execute(connection, "UPDATE users SET identity_id=? WHERE id=?", (identity["id"], user["id"]))
        _, menu = self.send("/start")
        self.assertEqual(menu["inline_keyboard"][0][0], {"text": "Открыть Остров", "web_app": {"url": self.web_app_url}})
        with self.database.connection() as connection:
            self.database.execute(connection, "UPDATE users SET access_revoked_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
        reply, menu = self.send("/start")
        self.assertIn("приостановлен", reply)
        self.assertEqual(menu, {"remove_keyboard": True})

    def test_webhook_secret_and_private_sender_match_are_required(self):
        app = FastAPI()
        app.include_router(create_router(self.database))
        settings = SimpleNamespace(telegram_webhook_secret="synthetic-secret", telegram_bot_token="synthetic-token",
                                   frontend_public_url=self.web_app_url)
        header = {"X-Telegram-Bot-Api-Secret-Token": "synthetic-secret"}
        with TestClient(app) as client, patch("backend.handlers.api.get_settings", return_value=settings), patch(
            "backend.services.telegram_admin_bot._send_message"
        ) as send:
            target = "/api/telegram/webhook"
            self.assertEqual(client.post(target, json=self.payload("/start")).status_code, 403)
            self.assertEqual(client.post(target, json=self.payload("/start", chat_type="group"), headers=header).status_code, 200)
            self.assertEqual(client.post(target, json=self.payload("/start", sender_id=900123), headers=header).status_code, 200)
            send.assert_not_called()
            self.assertIsNone(self.database.find_user(self.telegram_id))
            self.assertEqual(client.post(target, json=self.payload("/start"), headers=header).status_code, 200)
            send.assert_called_once()

    def test_repeated_commands_are_throttled_in_storage(self):
        self.send("/start")
        for _ in range(30):
            self.deliver(self.payload("/status"))
        self.assertEqual(len(self.messages), 20)

    def test_notification_failure_does_not_expose_transport_error(self):
        with patch("backend.services.telegram_admin_bot._send_message", side_effect=URLError("synthetic transport failure")):
            self.assertFalse(notify_registration_result("synthetic-token", self.telegram_id, "approved", web_app_url=self.web_app_url))


if __name__ == "__main__":
    unittest.main()
