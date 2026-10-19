import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import perform_apple_transfer  # noqa: E402


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


class AppleTransferTests(unittest.TestCase):
    def test_new_playlist_matches_isrc_and_search(self):
        apple = FakeApple(
            by_isrc={"ISRC1": song(101, "Song 1", "Artist", "ISRC1")},
            by_term={"Song 2 Artist": [song(102, "Song 2", "Artist")]},
        )
        tracks = [
            make_track(1, isrc="ISRC1"),
            make_track(2, title="Song 2"),
            make_track(3, title="Unknown"),
        ]
        job = make_job()
        stats = make_stats()
        result = perform_apple_transfer(
            apple,
            job,
            tracks,
            {"mode": "new", "name": "Transfert", "description": "Desc", "public": True},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            stats,
            [],
        )

        self.assertEqual(apple.created_name, "Transfert")
        self.assertEqual(apple.created_description, "Desc")
        self.assertTrue(apple.created_public)
        self.assertEqual(apple.created_song_ids, [])
        self.assertEqual(apple.added_batches, [["101", "102"]])
        self.assertEqual(result["added"], 2)
        self.assertTrue(result["created"])
        self.assertEqual(result["playlist_id"], "p.NEW")
        self.assertEqual(result["stats"]["matched_isrc"], 1)
        self.assertEqual(result["stats"]["matched_search"], 1)
        self.assertEqual(result["stats"]["unmatched"], 1)
        self.assertEqual(result["unmatched_preview"], ["Artist — Unknown"])

    def test_dry_run_changes_nothing(self):
        apple = FakeApple(by_isrc={"ISRC1": song(101, "Song 1", "Artist", "ISRC1")})
        job = make_job()
        result = perform_apple_transfer(
            apple,
            job,
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
        result = perform_apple_transfer(
            apple,
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
        result = perform_apple_transfer(
            apple,
            make_job(),
            tracks,
            {"mode": "new", "name": "Grosse playlist"},
            {"dedupe": True, "dedupe_isrc": False, "dry_run": False},
            make_stats(),
            [],
        )

        self.assertEqual(apple.created_song_ids, [])
        self.assertEqual([len(batch) for batch in apple.added_batches], [100, 50])
        self.assertEqual(result["added"], 150)
        self.assertEqual(result["to_add"], 150)


if __name__ == "__main__":
    unittest.main()
