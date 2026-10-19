"""Normalisation et rapprochement de morceaux entre fournisseurs.

Les catalogues ne partagent pas toujours les mêmes identifiants. L'ISRC reste
la meilleure clé de correspondance ; quand il manque, on compare le titre et
l'artiste avec une tolérance pour les variantes courantes (feat., remaster…).
"""

import re
import unicodedata
from difflib import SequenceMatcher

_PARENTHESES_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT_RE = re.compile(r"\b(?:feat\.?|featuring|ft\.?|avec)\b.*$", re.IGNORECASE)
_DASH_SUFFIX_RE = re.compile(
    r"\s+-\s+(?:"
    r"remaster(?:ed|isé|isée)?(?:\s+\d{4})?"
    r"|(?:\d{4}\s+)?remaster(?:ed|isé|isée)?"
    r"|album version|single version|radio edit|bonus track"
    r"|mono|stereo"
    r")\b.*$",
    re.IGNORECASE,
)
_PUNCTUATION_RE = re.compile(r"[^a-z0-9\s]")
_SPACES_RE = re.compile(r"\s+")


def normalize(text):
    """Retourne une forme comparable : sans accents, ponctuation ni bruit."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("&", " and ")
    text = _PARENTHESES_RE.sub(" ", text)
    text = _FEAT_RE.sub(" ", text)
    text = _DASH_SUFFIX_RE.sub(" ", text)
    text = text.replace("'", "").replace("’", "")
    text = _PUNCTUATION_RE.sub(" ", text)
    return _SPACES_RE.sub(" ", text).strip()


def _similarity(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    sequence = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return max(sequence, jaccard)


def _candidate_attributes(candidate):
    return candidate.get("attributes") or candidate


def score_candidate(track, candidate):
    """Note de ressemblance (0..1) entre un morceau source et un résultat."""
    attributes = _candidate_attributes(candidate)
    title_score = _similarity(
        normalize(track.get("title")),
        normalize(attributes.get("name") or attributes.get("title")),
    )

    source_artists = [normalize(artist) for artist in track.get("artists") or [] if artist]
    candidate_artist = normalize(
        attributes.get("artistName")
        or attributes.get("artist")
        or attributes.get("artist_name")
    )
    artist_score = max(
        (_similarity(artist, candidate_artist) for artist in source_artists),
        default=0.0,
    )

    score = 0.7 * title_score + 0.3 * artist_score

    source_duration = track.get("duration_ms")
    candidate_duration = attributes.get("durationInMillis") or attributes.get("duration_ms")
    if source_duration and candidate_duration:
        difference = abs(int(source_duration) - int(candidate_duration)) / 1000.0
        if difference <= 3:
            score += 0.05
        elif difference > 20:
            score -= 0.12

    return max(0.0, min(1.0, score))


def pick_best_candidate(track, candidates, threshold=0.62):
    """Retourne (meilleur résultat, note) ou (None, meilleure note)."""
    best = None
    best_score = 0.0
    for candidate in candidates or []:
        candidate_score = score_candidate(track, candidate)
        if candidate_score > best_score:
            best = candidate
            best_score = candidate_score
    if best is not None and best_score >= threshold:
        return best, best_score
    return None, best_score
