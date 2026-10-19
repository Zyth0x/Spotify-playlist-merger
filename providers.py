"""Adaptateurs de destination pour les fournisseurs autres que Spotify.

Chaque adaptateur expose la même interface, utilisée par le moteur de
transfert générique dans ``app.py`` :

- ``existing_tracks(playlist_id)`` : identifiants et ISRC déjà présents ;
- ``resolve_track(track)`` : retrouve un morceau dans le catalogue cible ;
- ``create_playlist(...)`` / ``add_tracks(...)`` : écriture ;
- ``playlist_url(playlist_id)`` : lien vers la playlist.
"""

from apple_client import AppleMusicAPIError
from matching import pick_best_candidate


class DestinationAdapter:
    provider_id = ""
    label = ""
    batch_size = 100

    def existing_tracks(self, playlist_id):
        return {"ids": set(), "isrcs": set()}

    def resolve_track(self, track):
        raise NotImplementedError

    def create_playlist(self, name, description="", public=False):
        raise NotImplementedError

    def add_tracks(self, playlist_id, track_ids):
        raise NotImplementedError

    def playlist_url(self, playlist_id):
        raise NotImplementedError


def deezer_candidate(track):
    """Normalise un morceau Deezer pour le moteur de rapprochement."""
    artist = track.get("artist")
    artist_name = artist.get("name") if isinstance(artist, dict) else artist
    duration = track.get("duration")
    return {
        "id": str(track.get("id")),
        "attributes": {
            "name": track.get("title"),
            "artistName": artist_name,
            "durationInMillis": int(duration) * 1000 if duration else None,
            "isrc": track.get("isrc"),
        },
    }


def youtube_candidate(track):
    """Normalise un résultat YouTube Music pour le rapprochement."""
    artists = [
        artist.get("name")
        for artist in track.get("artists") or []
        if isinstance(artist, dict) and artist.get("name")
    ]
    duration = track.get("duration_seconds")
    return {
        "id": str(track.get("videoId")),
        "attributes": {
            "name": track.get("title"),
            "artistName": ", ".join(artists),
            "durationInMillis": int(duration) * 1000 if duration else None,
        },
    }


class AppleMusicDestination(DestinationAdapter):
    provider_id = "apple_music"
    label = "Apple Music"

    def __init__(self, client):
        self.client = client

    def existing_tracks(self, playlist_id):
        data = self.client.playlist_existing_tracks(playlist_id)
        return {
            "ids": data.get("catalog_ids", set()),
            "isrcs": data.get("isrcs", set()),
        }

    def resolve_track(self, track):
        isrc = (track.get("isrc") or "").strip()
        if isrc:
            try:
                song = self.client.search_song_by_isrc(isrc)
            except AppleMusicAPIError as exc:
                if exc.status in (401, 403, 429) or exc.status >= 500:
                    raise
                song = None
            if song:
                return str(song.get("id")), "isrc"

        term = "{} {}".format(
            track.get("title") or "",
            (track.get("artists") or [""])[0],
        ).strip()
        if not term:
            return None, None
        candidates = self.client.search_songs(term, limit=10)
        song, _score = pick_best_candidate(track, candidates)
        if song:
            return str(song.get("id")), "search"
        return None, None

    def create_playlist(self, name, description="", public=False):
        playlist = self.client.create_playlist(name, description, public)
        playlist_id = playlist.get("id")
        playlist_name = (playlist.get("attributes") or {}).get("name") or name
        return {"id": playlist_id, "name": playlist_name}

    def add_tracks(self, playlist_id, track_ids):
        return self.client.add_tracks(playlist_id, track_ids)

    def playlist_url(self, playlist_id):
        return "https://music.apple.com/library/playlist/{}".format(playlist_id)


class DeezerDestination(DestinationAdapter):
    provider_id = "deezer"
    label = "Deezer"

    def __init__(self, client):
        self.client = client

    def existing_tracks(self, playlist_id):
        return self.client.playlist_existing_tracks(playlist_id)

    def resolve_track(self, track):
        isrc = (track.get("isrc") or "").strip()
        if isrc:
            song = self.client.search_track_by_isrc(isrc)
            if song:
                return str(song.get("id")), "isrc"

        term = "{} {}".format(
            track.get("title") or "",
            (track.get("artists") or [""])[0],
        ).strip()
        if not term:
            return None, None
        candidates = [
            deezer_candidate(result)
            for result in self.client.search_tracks(term, limit=10)
        ]
        song, _score = pick_best_candidate(track, candidates)
        if song:
            return str(song.get("id")), "search"
        return None, None

    def create_playlist(self, name, description="", public=False):
        playlist = self.client.create_playlist(name, description)
        return {
            "id": str(playlist.get("id")) if playlist.get("id") else None,
            "name": playlist.get("title") or name,
        }

    def add_tracks(self, playlist_id, track_ids):
        return self.client.add_tracks(playlist_id, track_ids)

    def playlist_url(self, playlist_id):
        return self.client.playlist_url(playlist_id)


class YouTubeMusicDestination(DestinationAdapter):
    provider_id = "youtube_music"
    label = "YouTube Music"

    def __init__(self, client):
        self.client = client

    def existing_tracks(self, playlist_id):
        return self.client.playlist_existing_tracks(playlist_id)

    def resolve_track(self, track):
        term = "{} {}".format(
            track.get("title") or "",
            (track.get("artists") or [""])[0],
        ).strip()
        if not term:
            return None, None
        candidates = [
            youtube_candidate(result)
            for result in self.client.search_tracks(term, limit=10)
        ]
        song, _score = pick_best_candidate(track, candidates)
        if song:
            return str(song.get("id")), "search"
        return None, None

    def create_playlist(self, name, description="", public=False):
        playlist = self.client.create_playlist(name, description, public)
        playlist_id = playlist.get("id")
        playlist_name = (playlist.get("attributes") or {}).get("name") or name
        return {"id": playlist_id, "name": playlist_name}

    def add_tracks(self, playlist_id, track_ids):
        return self.client.add_tracks(playlist_id, track_ids)

    def playlist_url(self, playlist_id):
        return self.client.playlist_url(playlist_id)
