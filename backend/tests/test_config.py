import os
import unittest
from unittest.mock import patch

from backend.config import Settings


class SettingsCorsTests(unittest.TestCase):
    def test_development_accepts_vinext_preview_origin(self):
        with patch.dict(os.environ, {"APP_ENV": "development", "CORS_ORIGINS": "http://localhost:3000"}, clear=False):
            settings = Settings.from_env()
        self.assertEqual(settings.cors_origins, ("http://localhost:3000", "http://localhost:4173"))

    def test_production_does_not_expand_configured_origins(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "CORS_ORIGINS": "https://islandquiz.online"}, clear=False):
            settings = Settings.from_env()
        self.assertEqual(settings.cors_origins, ("https://islandquiz.online",))


if __name__ == "__main__":
    unittest.main()
