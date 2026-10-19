import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from youtube_client import YouTubeMusicClient  # noqa: E402


class FakeYTMusic:
    def __init__(self):
        self.created = None
        self.added = None

    def get_library_playlists(self, limit=None):
        return [
            {"playlistId": "PL1", "title": "Playlist", "count": 2},
            {"title": "Sans identifiant"},
        ]

    def get_playlist(self, playlistId, limit=None):
        return {
            "tracks": [
                {"videoId": "video1", "title": "Song 1"},
                {"videoId": None, "title": "Indisponible"},
            ]
        }

    def search(self, query, filter=None, limit=20):
        return [
            {
                "videoId": "video1",
                "title": "Song",
                "artists": [{"name": "Artist"}],
                "duration_seconds": 200,
            }
        ]

    def create_playlist(self, title, description, privacy_status="PRIVATE"):
        self.created = (title, description, privacy_status)
        return "PLNEW"

    def add_playlist_items(self, playlistId, videoIds, duplicates=False):
        self.added = (playlistId, list(videoIds), duplicates)
        return "STATUS_SUCCEEDED"


class YouTubeMusicClientTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.token_path = os.path.join(self.temp_dir, "oauth.json")
        with open(self.token_path, "w", encoding="utf-8") as handle:
            json.dump({
                "scope": "https://www.googleapis.com/auth/youtube",
                "token_type": "Bearer",
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": int(time.time()) + 3600,
                "expires_in": 3600,
            }, handle)
        self.client = YouTubeMusicClient(
            {"youtube_music": {"client_id": "client", "client_secret": "secret"}},
            self.token_path,
        )
        self.fake = FakeYTMusic()
        self.client._client_instance = self.fake

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_playlists_filters_items_without_id(self):
        playlists = self.client.all_playlists()
        self.assertEqual([playlist["playlistId"] for playlist in playlists], ["PL1"])

    def test_playlist_tracks_filters_unavailable_items(self):
        tracks = self.client.playlist_tracks("PL1")
        self.assertEqual([track["videoId"] for track in tracks], ["video1"])
        self.assertEqual(
            self.client.playlist_existing_tracks("PL1"),
            {"ids": {"video1"}, "isrcs": set()},
        )

    def test_search_returns_songs(self):
        tracks = self.client.search_tracks("Song Artist")
        self.assertEqual(tracks[0]["videoId"], "video1")

    def test_create_playlist_and_add_tracks(self):
        playlist = self.client.create_playlist("Ma playlist", "Desc", True)
        self.assertEqual(playlist["id"], "PLNEW")
        self.assertEqual(self.fake.created, ("Ma playlist", "Desc", "PUBLIC"))
        self.client.add_tracks("PLNEW", ["video1", "video2"])
        self.assertEqual(
            self.fake.added,
            ("PLNEW", ["video1", "video2"], False),
        )


if __name__ == "__main__":
    unittest.main()
