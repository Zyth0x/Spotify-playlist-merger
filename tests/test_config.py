import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

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
            "DEEZER_TOKENS_PATH": app_module.DEEZER_TOKENS_PATH,
            "YOUTUBE_TOKEN_PATH": app_module.YOUTUBE_TOKEN_PATH,
        }
        app_module.DATA_DIR = self.temp_dir
        app_module.CONFIG_PATH = os.path.join(self.temp_dir, "config.json")
        app_module.TOKENS_PATH = os.path.join(self.temp_dir, "tokens.json")
        app_module.APPLE_KEY_PATH = os.path.join(self.temp_dir, "apple_key.p8")
        app_module.APPLE_TOKENS_PATH = os.path.join(self.temp_dir, "apple_tokens.json")
        app_module.DEEZER_TOKENS_PATH = os.path.join(self.temp_dir, "deezer_tokens.json")
        app_module.YOUTUBE_TOKEN_PATH = os.path.join(self.temp_dir, "ytmusic_oauth.json")
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

    def test_deezer_config_is_saved_without_losing_spotify(self):
        self.client.post("/api/client-id", json={"client_id": "spotify-client"})
        response = self.client.post("/api/deezer/config", json={
            "app_id": "123456",
            "app_secret": "secret",
        })
        self.assertEqual(response.status_code, 200)
        with open(app_module.CONFIG_PATH, encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(config["client_id"], "spotify-client")
        self.assertEqual(config["deezer"]["app_id"], "123456")
        self.assertEqual(config["deezer"]["app_secret"], "secret")
        state = self.client.get("/api/state").get_json()
        self.assertTrue(state["deezer"]["configured"])
        self.assertFalse(state["deezer"]["authenticated"])

    def test_youtube_config_is_saved_without_losing_deezer(self):
        self.client.post("/api/deezer/config", json={
            "app_id": "123456",
            "app_secret": "secret",
        })
        response = self.client.post("/api/youtube/config", json={
            "client_id": "google-client",
            "client_secret": "google-secret",
        })
        self.assertEqual(response.status_code, 200)
        with open(app_module.CONFIG_PATH, encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(config["deezer"]["app_id"], "123456")
        self.assertEqual(config["youtube_music"]["client_id"], "google-client")
        state = self.client.get("/api/state").get_json()
        self.assertTrue(state["youtube_music"]["configured"])
        self.assertFalse(state["youtube_music"]["authenticated"])

    def test_deezer_login_redirects_to_oauth(self):
        self.client.post("/api/deezer/config", json={
            "app_id": "123456",
            "app_secret": "secret",
        })
        response = self.client.get("/deezer/login")
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertIn("connect.deezer.com/oauth/auth.php", location)
        self.assertIn("state=", location)

    def test_youtube_device_requires_configuration(self):
        response = self.client.post("/api/youtube/device")
        self.assertEqual(response.status_code, 400)

    def test_playlists_rejects_unknown_provider(self):
        response = self.client.get("/api/playlists?provider=unknown")
        self.assertEqual(response.status_code, 400)

    def test_playlists_requires_provider_configuration(self):
        response = self.client.get("/api/playlists?provider=deezer")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Deezer", response.get_json()["error"])

    def test_playlists_uses_selected_source_provider(self):
        class FakeProvider:
            label = "Deezer"

            def is_configured(self):
                return True

            def is_authenticated(self):
                return True

            def list_playlists(self):
                return [{
                    "id": "dz1",
                    "name": "Playlist Deezer",
                    "track_count": 1,
                    "can_read": True,
                    "can_edit": True,
                }]

        with mock.patch.object(
            app_module, "get_music_provider", return_value=FakeProvider()
        ):
            response = self.client.get("/api/playlists?provider=deezer")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["provider"], "deezer")
        self.assertEqual(payload["playlists"][0]["name"], "Playlist Deezer")

    def test_merge_rejects_unknown_source(self):
        response = self.client.post("/api/merge", json={
            "source": "unknown",
            "playlist_ids": ["playlist"],
            "target": {"provider": "spotify", "mode": "new", "name": "Test"},
        })
        self.assertEqual(response.status_code, 400)

    def test_merge_requires_source_connection(self):
        self.client.post("/api/client-id", json={"client_id": "spotify-client"})
        response = self.client.post("/api/merge", json={
            "source": "spotify",
            "playlist_ids": ["playlist"],
            "target": {"provider": "spotify", "mode": "new", "name": "Test"},
        })
        self.assertEqual(response.status_code, 401)
        self.assertIn("Connectez votre compte Spotify", response.get_json()["error"])

    def test_youtube_device_flow_saves_token(self):
        self.client.post("/api/youtube/config", json={
            "client_id": "google-client",
            "client_secret": "google-secret",
        })
        fake_code = {
            "device_code": "device-code",
            "user_code": "ABCD-EFGH",
            "verification_url": "https://www.google.com/device",
            "interval": 5,
            "expires_in": 1800,
        }
        with mock.patch.object(
            app_module, "start_device_authorization", return_value=fake_code
        ):
            response = self.client.post("/api/youtube/device")
        self.assertEqual(response.status_code, 200)
        flow_id = response.get_json()["flow_id"]
        self.assertEqual(response.get_json()["user_code"], "ABCD-EFGH")

        fake_token = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/youtube",
            "token_type": "Bearer",
        }
        with mock.patch.object(
            app_module, "poll_device_authorization", return_value=fake_token
        ):
            response = self.client.get(
                "/api/youtube/device/status?flow_id={}".format(flow_id)
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["youtube_music"]["authenticated"])
        with open(app_module.YOUTUBE_TOKEN_PATH, encoding="utf-8") as handle:
            token = json.load(handle)
        self.assertEqual(token["access_token"], "access-token")
        self.assertEqual(token["refresh_token"], "refresh-token")
        self.assertEqual(
            os.stat(app_module.YOUTUBE_TOKEN_PATH).st_mode & 0o777, 0o600
        )


if __name__ == "__main__":
    unittest.main()
