import tempfile
import unittest
from pathlib import Path

from backend.database import Database
from backend.services.telegram_admin_bot import handle_admin_code_update


class TelegramAdminBotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "admin.db")
        self.database.initialize()
        with self.database.connection() as connection:
            user = self.database.execute(
                connection,
                "INSERT INTO users(telegram_user_id, role) VALUES (?, 'admin') RETURNING id",
                (991001,),
            ).fetchone()
            self.user_id = user["id"]
            self.database.execute(connection, "INSERT INTO user_roles(user_id, role) VALUES (?, 'admin')", (self.user_id,))

    def tearDown(self):
        self.temp.cleanup()

    def test_admin_private_command_sends_one_time_code(self):
        messages = []
        handled = handle_admin_code_update(
            self.database,
            {"message": {"chat": {"id": 991001, "type": "private"}, "from": {"id": 991001}, "text": "/admincode"}},
            "test-token",
            lambda token, chat_id, text: messages.append((token, chat_id, text)),
        )

        self.assertTrue(handled)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][0:2], ("test-token", 991001))
        self.assertIn("Одноразовый", messages[0][2])
        code = messages[0][2].splitlines()[1]
        session_token, user_id = self.database.consume_admin_login_challenge(code)
        self.assertEqual(user_id, self.user_id)
        self.assertTrue(session_token)

    def test_non_admin_does_not_receive_a_code(self):
        messages = []
        handled = handle_admin_code_update(
            self.database,
            {"message": {"chat": {"id": 991002, "type": "private"}, "from": {"id": 991002}, "text": "/admincode@my_bot"}},
            "test-token",
            lambda token, chat_id, text: messages.append(text),
        )

        self.assertTrue(handled)
        self.assertEqual(messages, ["Доступ разрешён только пользователю с ролью admin."])

    def test_group_command_is_ignored_without_sending_code(self):
        messages = []
        handled = handle_admin_code_update(
            self.database,
            {"message": {"chat": {"id": -1001, "type": "group"}, "from": {"id": 991001}, "text": "/admincode"}},
            "test-token",
            lambda token, chat_id, text: messages.append(text),
        )

        self.assertTrue(handled)
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
