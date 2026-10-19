import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import perform_destination_transfer  # noqa: E402
from providers import (  # noqa: E402
    AppleMusicProvider,
    DeezerProvider,
    SpotifyProvider,
    YouTubeMusicProvider,
    deezer_candidate,
    youtube_candidate,
)


class FakeSource:
    provider_id = "spotify"
    label = "Spotify"


def make_track(index, isrc=None, title=None, artist="Artist"):
    return {
        "uri": "spotify:track:{}".format(index),
        "isrc": isrc,
        "title": title or "Song {}".format(index),
        "artists": [artist],
        "album": "Album",
        "duration_ms": 200000,
        "label": "{} — {}".format(artist, title or "Song {}".format(index)),
    }


def make_stats():
    return {
        "tracks_read": 0,
        "duplicates": 0,
        "episodes": 0,
        "local_files": 0,
        "unavailable": 0,
        "already_in_target": 0,
        "matched_direct": 0,
        "matched_isrc": 0,
        "matched_search": 0,
        "unmatched": 0,
        "skipped_playlists": [],
    }


def make_job():
    return {
        "cancel": False,
        "added": 0,
        "message": "",
        "phase": "collect",
        "progress": {"done": 0, "total": 0},
    }


def song(song_id, name="Song", artist="Artist", isrc=None):
    attributes = {
        "name": name,
        "artistName": artist,
        "durationInMillis": 200000,
    }
    if isrc:
        attributes["isrc"] = isrc
    return {"id": str(song_id), "type": "songs", "attributes": attributes}


def deezer_track(track_id, name="Song", artist="Artist", isrc=None):
    data = {
        "id": track_id,
        "title": name,
        "artist": {"name": artist},
        "duration": 200,
    }
    if isrc:
        data["isrc"] = isrc
    return data


def youtube_track(video_id, name="Song", artist="Artist"):
    return {
        "videoId": video_id,
        "title": name,
        "artists": [{"name": artist}],
        "duration_seconds": 200,
    }


class FakeApple:
    def __init__(self, by_isrc=None, by_term=None, existing=None):
        self.by_isrc = by_isrc or {}
        self.by_term = by_term or {}
        self.existing = existing or {"catalog_ids": set(), "isrcs": set()}
        self.created_name = None
        self.created_description = None
        self.created_public = None
        self.created_song_ids = []
        self.added_batches = []

    def playlist_existing_tracks(self, playlist_id):
        return self.existing

    def search_song_by_isrc(self, isrc):
        return self.by_isrc.get(isrc.upper())

    def search_songs(self, term, limit=10):
        return self.by_term.get(term, [])

    def create_playlist(self, name, description="", public=False, song_ids=None):
        self.created_name = name
        self.created_description = description
        self.created_public = public
        self.created_song_ids = list(song_ids or [])
        return {
            "id": "p.NEW",
            "type": "library-playlists",
            "attributes": {"name": name},
        }

    def add_tracks(self, playlist_id, song_ids):
        self.added_batches.append(list(song_ids))


class FakeDeezer:
    def __init__(self, by_isrc=None, by_term=None, existing=None):
        self.by_isrc = by_isrc or {}
        self.by_term = by_term or {}
        self.existing = existing or {"ids": set(), "isrcs": set()}
        self.created_name = None
        self.created_description = None
        self.added_batches = []

    def playlist_existing_tracks(self, playlist_id):
        return self.existing

    def search_track_by_isrc(self, isrc):
        return self.by_isrc.get(isrc.upper())

    def search_tracks(self, term, limit=10):
        return self.by_term.get(term, [])

    def create_playlist(self, name, description=""):
        self.created_name = name
        self.created_description = description
        return {"id": 42, "title": name}

    def add_tracks(self, playlist_id, track_ids):
        self.added_batches.append(list(track_ids))

    def playlist_url(self, playlist_id):
        return "https://www.deezer.com/playlist/{}".format(playlist_id)


class FakeYouTube:
    def __init__(self, by_term=None, existing=None):
        self.by_term = by_term or {}
        self.existing = existing or {"ids": set(), "isrcs": set()}
        self.created_name = None
        self.created_description = None
        self.created_public = None
        self.added_batches = []

    def playlist_existing_tracks(self, playlist_id):
        return self.existing

    def search_tracks(self, term, limit=10):
        return self.by_term.get(term, [])

    def create_playlist(self, name, description="", public=False):
        self.created_name = name
        self.created_description = description
        self.created_public = public
        return {"id": "PLNEW", "attributes": {"name": name}}

    def add_tracks(self, playlist_id, track_ids):
        self.added_batches.append(list(track_ids))

    def playlist_url(self, playlist_id):
        return "https://music.youtube.com/playlist?list={}".format(playlist_id)


class FakeAppleSource:
    provider_id = "apple_music"
    label = "Apple Music"


class FakeSpotify:
    def __init__(self, by_isrc=None, by_term=None, existing_tracks=None):
        self.by_isrc = by_isrc or {}
        self.by_term = by_term or {}
        self.existing_tracks = existing_tracks or []
        self.created_name = None
        self.created_public = None
        self.created_description = None
        self.added_batches = []

    def playlist_items(self, playlist_id):
        for track in self.existing_tracks:
            yield {"item": track}

    def search_track_by_isrc(self, isrc):
        return self.by_isrc.get(isrc.upper())

    def search_tracks(self, term, limit=10):
        return self.by_term.get(term, [])

    def create_playlist(self, name, public, description=""):
        self.created_name = name
        self.created_public = public
        self.created_description = description
        return {"id": "spnew", "name": name}

    def add_items(self, playlist_id, uris):
        self.added_batches.append(list(uris))


class ExternalTransferTests(unittest.TestCase):
    def test_apple_new_playlist_matches_isrc_and_search(self):
        apple = FakeApple(
            by_isrc={"ISRC1": song(101, "Song 1", "Artist", "ISRC1")},
            by_term={"Song 2 Artist": [song(102, "Song 2", "Artist")]},
        )
        tracks = [
            make_track(1, isrc="ISRC1"),
            make_track(2, title="Song 2"),
            make_track(3, title="Unknown"),
        ]
        result = perform_destination_transfer(
            FakeSource(),
            AppleMusicProvider(apple),
            make_job(),
            tracks,
            {"mode": "new", "name": "Transfert", "description": "Desc", "public": True},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(apple.created_name, "Transfert")
        self.assertEqual(apple.created_description, "Desc")
        self.assertTrue(apple.created_public)
        self.assertEqual(apple.added_batches, [["101", "102"]])
        self.assertEqual(result["added"], 2)
        self.assertTrue(result["created"])
        self.assertEqual(result["provider"], "apple_music")
        self.assertEqual(result["provider_label"], "Apple Music")
        self.assertEqual(result["playlist_id"], "p.NEW")
        self.assertEqual(result["stats"]["matched_isrc"], 1)
        self.assertEqual(result["stats"]["matched_search"], 1)
        self.assertEqual(result["stats"]["unmatched"], 1)
        self.assertEqual(result["unmatched_preview"], ["Artist — Unknown"])

    def test_dry_run_changes_nothing(self):
        apple = FakeApple(by_isrc={"ISRC1": song(101, "Song 1", "Artist", "ISRC1")})
        result = perform_destination_transfer(
            FakeSource(),
            AppleMusicProvider(apple),
            make_job(),
            [make_track(1, isrc="ISRC1")],
            {"mode": "new", "name": "Brouillon"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": True},
            make_stats(),
            [],
        )

        self.assertIsNone(apple.created_name)
        self.assertEqual(apple.added_batches, [])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["to_add"], 1)
        self.assertEqual(result["added"], 0)
        self.assertIsNone(result["playlist_id"])

    def test_existing_playlist_skips_already_present_tracks(self):
        apple = FakeApple(
            by_isrc={
                "ISRC1": song(101, "Song 1", "Artist", "ISRC1"),
                "ISRC2": song(102, "Song 2", "Artist", "ISRC2"),
            },
            existing={"catalog_ids": {"101"}, "isrcs": {"ISRC1"}},
        )
        tracks = [make_track(1, isrc="ISRC1"), make_track(2, isrc="ISRC2")]
        result = perform_destination_transfer(
            FakeSource(),
            AppleMusicProvider(apple),
            make_job(),
            tracks,
            {"mode": "existing", "id": "p.OLD", "name": "Ancienne"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(result["stats"]["already_in_target"], 1)
        self.assertEqual(apple.added_batches, [["102"]])
        self.assertEqual(result["added"], 1)
        self.assertFalse(result["created"])
        self.assertEqual(result["playlist_id"], "p.OLD")
        self.assertIn("/library/playlist/p.OLD", result["playlist_url"])

    def test_playlist_is_created_in_batches_of_100(self):
        by_isrc = {}
        tracks = []
        for index in range(150):
            isrc = "ISRC{:04d}".format(index)
            by_isrc[isrc] = song(1000 + index, "Song", "Artist", isrc)
            tracks.append(make_track(index, isrc=isrc))

        apple = FakeApple(by_isrc=by_isrc)
        result = perform_destination_transfer(
            FakeSource(),
            AppleMusicProvider(apple),
            make_job(),
            tracks,
            {"mode": "new", "name": "Grosse playlist"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual([len(batch) for batch in apple.added_batches], [100, 50])
        self.assertEqual(result["added"], 150)
        self.assertEqual(result["to_add"], 150)

    def test_deezer_transfer_uses_isrc_then_search(self):
        deezer = FakeDeezer(
            by_isrc={"ISRC1": deezer_track(101, "Song 1", "Artist", "ISRC1")},
            by_term={"Song 2 Artist": [deezer_track(102, "Song 2", "Artist")]},
        )
        tracks = [make_track(1, isrc="ISRC1"), make_track(2, title="Song 2")]
        result = perform_destination_transfer(
            FakeSource(),
            DeezerProvider(deezer),
            make_job(),
            tracks,
            {"mode": "new", "name": "Deezer", "description": "Desc"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(deezer.created_name, "Deezer")
        self.assertEqual(deezer.added_batches, [["101", "102"]])
        self.assertEqual(result["provider"], "deezer")
        self.assertEqual(result["playlist_url"], "https://www.deezer.com/playlist/42")
        self.assertEqual(result["stats"]["matched_isrc"], 1)
        self.assertEqual(result["stats"]["matched_search"], 1)
        self.assertEqual(result["added"], 2)

    def test_deezer_existing_playlist_dedupes_by_id(self):
        deezer = FakeDeezer(
            by_isrc={"ISRC2": deezer_track(102, "Song 2", "Artist", "ISRC2")},
            existing={"ids": {"101"}, "isrcs": set()},
        )
        deezer.by_isrc["ISRC1"] = deezer_track(101, "Song 1", "Artist", "ISRC1")
        result = perform_destination_transfer(
            FakeSource(),
            DeezerProvider(deezer),
            make_job(),
            [make_track(1, isrc="ISRC1"), make_track(2, isrc="ISRC2")],
            {"mode": "existing", "id": "999", "name": "Ancienne"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(result["stats"]["already_in_target"], 1)
        self.assertEqual(deezer.added_batches, [["102"]])
        self.assertEqual(result["playlist_id"], "999")

    def test_youtube_transfer_matches_by_search(self):
        youtube = FakeYouTube(
            by_term={"Song Artist": [youtube_track("video1", "Song", "Artist")]}
        )
        result = perform_destination_transfer(
            FakeSource(),
            YouTubeMusicProvider(youtube),
            make_job(),
            [make_track(1, title="Song")],
            {"mode": "new", "name": "YT", "public": True},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(youtube.created_name, "YT")
        self.assertTrue(youtube.created_public)
        self.assertEqual(youtube.added_batches, [["video1"]])
        self.assertEqual(result["provider"], "youtube_music")
        self.assertEqual(result["provider_label"], "YouTube Music")
        self.assertEqual(result["stats"]["matched_search"], 1)
        self.assertEqual(
            result["playlist_url"],
            "https://music.youtube.com/playlist?list=PLNEW",
        )

    def test_spotify_to_spotify_uses_direct_ids(self):
        spotify = FakeSpotify()
        track = make_track(1)
        track.update({
            "source_provider": "spotify",
            "source_key": "spotify:track:1",
            "direct_id": "spotify:track:1",
        })
        result = perform_destination_transfer(
            FakeSource(),
            SpotifyProvider(spotify),
            make_job(),
            [track],
            {"mode": "new", "name": "Spotify", "public": False},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(spotify.created_name, "Spotify")
        self.assertEqual(spotify.added_batches, [["spotify:track:1"]])
        self.assertEqual(result["provider"], "spotify")
        self.assertEqual(result["source_provider"], "spotify")
        self.assertEqual(result["stats"]["matched_direct"], 1)
        self.assertEqual(result["stats"]["matched_isrc"], 0)

    def test_apple_to_spotify_matches_by_isrc(self):
        spotify = FakeSpotify(
            by_isrc={
                "ISRC1": {
                    "uri": "spotify:track:found",
                    "name": "Song 1",
                    "artists": [{"name": "Artist"}],
                    "duration_ms": 200000,
                    "external_ids": {"isrc": "ISRC1"},
                }
            }
        )
        result = perform_destination_transfer(
            FakeAppleSource(),
            SpotifyProvider(spotify),
            make_job(),
            [make_track(1, isrc="ISRC1")],
            {"mode": "new", "name": "Depuis Apple"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(spotify.added_batches, [["spotify:track:found"]])
        self.assertEqual(result["stats"]["matched_isrc"], 1)
        self.assertEqual(result["source_provider"], "apple_music")
        self.assertEqual(result["playlist_url"], "https://open.spotify.com/playlist/spnew")

    def test_spotify_destination_dedupes_existing_uri(self):
        spotify = FakeSpotify(
            existing_tracks=[{
                "type": "track",
                "uri": "spotify:track:1",
                "external_ids": {"isrc": "ISRC1"},
            }]
        )
        track = make_track(1, isrc="ISRC1")
        track.update({
            "source_provider": "spotify",
            "source_key": "spotify:track:1",
            "direct_id": "spotify:track:1",
        })
        result = perform_destination_transfer(
            FakeSource(),
            SpotifyProvider(spotify),
            make_job(),
            [track],
            {"mode": "existing", "id": "playlist1", "name": "Existante"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(result["stats"]["already_in_target"], 1)
        self.assertEqual(spotify.added_batches, [])
        self.assertEqual(result["added"], 0)

    def test_candidate_normalization(self):
        deezer = deezer_candidate(deezer_track(1, "Song", "Artist", "ISRC"))
        self.assertEqual(deezer["id"], "1")
        self.assertEqual(deezer["attributes"]["name"], "Song")
        self.assertEqual(deezer["attributes"]["artistName"], "Artist")
        self.assertEqual(deezer["attributes"]["durationInMillis"], 200000)
        self.assertEqual(deezer["attributes"]["isrc"], "ISRC")

        youtube = youtube_candidate(youtube_track("video1", "Song", "Artist"))
        self.assertEqual(youtube["id"], "video1")
        self.assertEqual(youtube["attributes"]["artistName"], "Artist")
        self.assertEqual(youtube["attributes"]["durationInMillis"], 200000)


if __name__ == "__main__":
    unittest.main()
