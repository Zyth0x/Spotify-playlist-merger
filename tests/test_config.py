import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

import app as app_module  # noqa: E402


def make_private_key():
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.originals = {
            "DATA_DIR": app_module.DATA_DIR,
            "CONFIG_PATH": app_module.CONFIG_PATH,
            "TOKENS_PATH": app_module.TOKENS_PATH,
            "APPLE_KEY_PATH": app_module.APPLE_KEY_PATH,
            "APPLE_TOKENS_PATH": app_module.APPLE_TOKENS_PATH,
        }
        app_module.DATA_DIR = self.temp_dir
        app_module.CONFIG_PATH = os.path.join(self.temp_dir, "config.json")
        app_module.TOKENS_PATH = os.path.join(self.temp_dir, "tokens.json")
        app_module.APPLE_KEY_PATH = os.path.join(self.temp_dir, "apple_key.p8")
        app_module.APPLE_TOKENS_PATH = os.path.join(self.temp_dir, "apple_tokens.json")
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(app_module, name, value)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_apple_config_preserves_spotify_client_id(self):
        response = self.client.post("/api/client-id", json={"client_id": "spotify-client"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/apple/config", json={
            "team_id": "ABCDE12345",
            "key_id": "KEY1234567",
            "storefront": "fr",
            "private_key": make_private_key(),
        })
        self.assertEqual(response.status_code, 200)
        with open(app_module.CONFIG_PATH, encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(config["client_id"], "spotify-client")
        self.assertEqual(config["apple_music"]["team_id"], "ABCDE12345")
        self.assertEqual(config["apple_music"]["key_id"], "KEY1234567")
        self.assertEqual(config["apple_music"]["storefront"], "fr")
        self.assertEqual(os.stat(app_module.APPLE_KEY_PATH).st_mode & 0o777, 0o600)

    def test_invalid_private_key_is_rejected(self):
        response = self.client.post("/api/apple/config", json={
            "team_id": "ABCDE12345",
            "key_id": "KEY1234567",
            "storefront": "fr",
            "private_key": "not a key",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Clé privée", response.get_json()["error"])

    def test_developer_token_requires_configuration(self):
        response = self.client.get("/api/apple/developer-token")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
