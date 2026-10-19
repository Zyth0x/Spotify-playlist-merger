import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matching import normalize, pick_best_candidate, score_candidate  # noqa: E402


def track(title, artists, duration_ms=200000):
    return {
        "title": title,
        "artists": artists,
        "duration_ms": duration_ms,
    }


def candidate(name, artist, duration_ms=200000):
    return {
        "id": "1",
        "type": "songs",
        "attributes": {
            "name": name,
            "artistName": artist,
            "durationInMillis": duration_ms,
        },
    }


class NormalizeTests(unittest.TestCase):
    def test_accents_and_punctuation(self):
        self.assertEqual(normalize("Beyoncé — Déjà Vu"), "beyonce deja vu")

    def test_feat_and_parentheses(self):
        self.assertEqual(normalize("Song Title (feat. Other Artist)"), "song title")

    def test_remaster_suffix(self):
        self.assertEqual(normalize("Don't Stop Me Now - Remastered 2011"), "dont stop me now")

    def test_ampersand(self):
        self.assertEqual(normalize("Rock & Roll"), "rock and roll")


class MatchingTests(unittest.TestCase):
    def test_exact_match_scores_high(self):
        score = score_candidate(
            track("Entropy", ["Beach Bunny"]),
            candidate("Entropy", "Beach Bunny"),
        )
        self.assertGreater(score, 0.95)

    def test_feat_variation_matches(self):
        source = track("Song Title", ["Main Artist"])
        result = candidate("Song Title (feat. Guest)", "Main Artist")
        song, score = pick_best_candidate(source, [result])
        self.assertIsNotNone(song)
        self.assertGreaterEqual(score, 0.62)

    def test_different_song_is_rejected(self):
        source = track("Entropy", ["Beach Bunny"])
        result = candidate("Bohemian Rhapsody", "Queen")
        song, _score = pick_best_candidate(source, [result])
        self.assertIsNone(song)

    def test_best_candidate_is_selected(self):
        source = track("Entropy", ["Beach Bunny"])
        candidates = [
            candidate("Entropy", "Another Artist"),
            candidate("Entropy", "Beach Bunny"),
        ]
        song, score = pick_best_candidate(source, candidates)
        self.assertEqual(song["attributes"]["artistName"], "Beach Bunny")
        self.assertGreater(score, 0.95)

    def test_duration_penalty_for_live_version(self):
        source = track("Song", ["Artist"], duration_ms=180000)
        studio = candidate("Song", "Artist", duration_ms=181000)
        live = candidate("Song", "Artist", duration_ms=300000)
        studio_score = score_candidate(source, studio)
        live_score = score_candidate(source, live)
        self.assertGreater(studio_score, live_score)


if __name__ == "__main__":
    unittest.main()
