import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.config import Settings


class ConsolidatedProductionConfigurationTests(unittest.TestCase):
    def settings(self, url):
        with patch.dict(os.environ, {}, clear=True):
            return replace(Settings.from_env(), app_env="production", dev_auth_enabled=False,
                           telegram_bot_token="synthetic-token", database_url=url)

    def test_consolidated_production_only_accepts_approved_database(self):
        urls = ["postgresql://postgres@db.kmrcbewmxeqqxfauiwyy.supabase.co/postgres",
                "postgresql://postgres.kmrcbewmxeqqxfauiwyy@aws-1-eu-west-1.pooler.supabase.com:5432/postgres"]
        with patch.dict(os.environ, {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1"}, clear=True):
            for url in urls:
                self.settings(url).validate()
            for url in ["postgresql://postgres@127.0.0.1/postgres",
                        "postgresql://postgres.other@aws-1-eu-west-1.pooler.supabase.com/postgres",
                        "postgresql://postgres@db.other.supabase.co/postgres"]:
                with self.subTest(url=url), self.assertRaisesRegex(ValueError, "approved My Island"):
                    self.settings(url).validate()

    def test_local_and_readonly_flags_cannot_enable_production_writes(self):
        for flag in ["MY_ISLAND_SCHOOL_WORKSPACE", "MY_ISLAND_READ_ONLY_SCHOOL_WORKSPACE"]:
            with patch.dict(os.environ, {"MY_ISLAND_CONSOLIDATED_SCHEDULE": "1", flag: "1"}, clear=True):
                with self.assertRaisesRegex(ValueError, "local/read-only"):
                    self.settings("postgresql://postgres@db.kmrcbewmxeqqxfauiwyy.supabase.co/postgres").validate()
