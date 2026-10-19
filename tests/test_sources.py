import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import collect_tracks  # noqa: E402
from providers import (  # noqa: E402
    AppleMusicProvider,
    DeezerProvider,
    SpotifyProvider,
    YouTubeMusicProvider,
)


class FakeSpotifyClient:
    def __init__(self, tracks):
        self.tracks = tracks

    def playlist_items(self, playlist_id):
        for track in self.tracks:
            yield track


class FakeAppleClient:
    def __init__(self, tracks):
        self.tracks = tracks

    def playlist_tracks(self, playlist_id):
        return self.tracks


class FakeDeezerClient:
    def __init__(self, tracks):
        self.tracks = tracks

    def playlist_tracks(self, playlist_id):
        return self.tracks


class FakeYouTubeClient:
    def __init__(self, tracks):
        self.tracks = tracks

    def playlist_tracks(self, playlist_id):
        return self.tracks


def make_job():
    return {
        "cancel": False,
        "added": 0,
        "message": "",
        "phase": "collect",
        "progress": {"done": 0, "total": 0},
    }


class SourceReadTests(unittest.TestCase):
    def test_spotify_source_tracks_and_stats(self):
        provider = SpotifyProvider(FakeSpotifyClient([
            {
                "item": {
                    "type": "track",
                    "uri": "spotify:track:1",
                    "name": "Song",
                    "artists": [{"name": "Artist"}],
                    "album": {"name": "Album"},
                    "duration_ms": 200000,
                    "external_ids": {"isrc": "ISRC1"},
                }
            },
            {"item": {"type": "episode"}},
            {"item": {"type": "track", "is_local": True, "uri": "spotify:local:1"}},
            {"item": None},
        ]))
        result = provider.read_playlist("playlist")

        self.assertEqual(len(result["tracks"]), 1)
        track = result["tracks"][0]
        self.assertEqual(track["source_provider"], "spotify")
        self.assertEqual(track["source_key"], "spotify:track:1")
        self.assertEqual(track["isrc"], "ISRC1")
        self.assertEqual(track["artists"], ["Artist"])
        self.assertEqual(result["episodes"], 1)
        self.assertEqual(result["local_files"], 1)
        self.assertEqual(result["unavailable"], 1)

    def test_apple_source_uses_catalog_id(self):
        provider = AppleMusicProvider(FakeAppleClient([
            {
                "id": "i.library",
                "type": "library-songs",
                "attributes": {
                    "name": "Song",
                    "artistName": "Artist",
                    "albumName": "Album",
                    "durationInMillis": 200000,
                    "isrc": "ISRC1",
                    "playParams": {"catalogId": "123"},
                },
            },
            {"id": "bad", "type": "library-songs", "attributes": {}},
        ]))
        result = provider.read_playlist("playlist")

        self.assertEqual(len(result["tracks"]), 1)
        track = result["tracks"][0]
        self.assertEqual(track["source_key"], "123")
        self.assertEqual(track["direct_id"], "123")
        self.assertEqual(track["isrc"], "ISRC1")
        self.assertEqual(result["unavailable"], 1)

    def test_deezer_source_track(self):
        provider = DeezerProvider(FakeDeezerClient([
            {
                "id": 1,
                "title": "Song",
                "artist": {"name": "Artist"},
                "album": {"title": "Album"},
                "duration": 200,
                "isrc": "ISRC1",
            },
            {"id": None, "title": ""},
        ]))
        result = provider.read_playlist("playlist")

        self.assertEqual(len(result["tracks"]), 1)
        track = result["tracks"][0]
        self.assertEqual(track["source_key"], "1")
        self.assertEqual(track["direct_id"], "1")
        self.assertEqual(track["duration_ms"], 200000)
        self.assertEqual(result["unavailable"], 1)

    def test_youtube_source_track(self):
        provider = YouTubeMusicProvider(FakeYouTubeClient([
            {
                "videoId": "video1",
                "title": "Song",
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album"},
                "duration_seconds": 200,
            },
            {"videoId": None},
        ]))
        result = provider.read_playlist("playlist")

        self.assertEqual(len(result["tracks"]), 1)
        track = result["tracks"][0]
        self.assertEqual(track["source_key"], "video1")
        self.assertEqual(track["direct_id"], "video1")
        self.assertEqual(track["duration_ms"], 200000)
        self.assertEqual(result["unavailable"], 1)


class FakeSource:
    provider_id = "spotify"
    label = "Spotify"

    def __init__(self, tracks, status=None):
        self.tracks = tracks
        self.status = status

    def read_playlist(self, playlist_id):
        if self.status:
            exc = RuntimeError("provider error")
            exc.status = self.status
            raise exc
        return {
            "tracks": self.tracks,
            "episodes": 0,
            "local_files": 0,
            "unavailable": 0,
        }


class CollectTracksTests(unittest.TestCase):
    def track(self, key, isrc=None, title=None):
        return {
            "source_provider": "spotify",
            "source_key": key,
            "direct_id": key,
            "isrc": isrc,
            "title": title or key,
            "artists": ["Artist"],
            "label": "Artist — {}".format(title or key),
        }

    def test_collect_deduplicates_by_key_and_isrc(self):
        source = FakeSource([
            self.track("a", "ISRC1", "A"),
            self.track("a", "ISRC1", "A"),
            self.track("b", "ISRC1", "B"),
            self.track("c", "ISRC2", "C"),
        ])
        tracks, stats, preview = collect_tracks(
            source,
            make_job(),
            [{"id": "playlist", "name": "Playlist"}],
            {"dedupe": True, "dedupe_isrc": True},
        )

        self.assertEqual([track["source_key"] for track in tracks], ["a", "c"])
        self.assertEqual(stats["duplicates"], 2)
        self.assertEqual(len(preview), 2)
        self.assertEqual(stats["tracks_read"], 4)

    def test_collect_skips_unreadable_playlist(self):
        source = FakeSource([], status=403)
        tracks, stats, preview = collect_tracks(
            source,
            make_job(),
            [{"id": "playlist", "name": "Privée"}],
            {"dedupe": True, "dedupe_isrc": False},
        )

        self.assertEqual(tracks, [])
        self.assertEqual(stats["skipped_playlists"], ["Privée"])
        self.assertEqual(preview, [])


if __name__ == "__main__":
    unittest.main()
