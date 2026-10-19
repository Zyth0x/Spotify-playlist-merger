"""Fournisseurs de musique : lecture des playlists et écriture des destinations.

Chaque fournisseur expose la même interface, utilisée par le moteur de
transfert générique dans ``app.py`` :

- ``list_playlists`` / ``read_playlist`` : côté source ;
- ``existing_tracks`` / ``resolve_track`` / ``create_playlist`` / ``add_tracks`` :
  côté destination.

Les morceaux sont représentés par des dictionnaires normalisés contenant
notamment ``source_provider``, ``source_key``, ``direct_id``, ``isrc``,
``title``, ``artists`` et ``duration_ms``.
"""

from apple_client import AppleMusicAPIError
from matching import pick_best_candidate


def _artist_names(value):
    if isinstance(value, list):
        names = []
        for artist in value:
            if isinstance(artist, dict) and artist.get("name"):
                names.append(artist["name"])
            elif isinstance(artist, str):
                names.append(artist)
        return names
    if isinstance(value, str) and value:
        return [value]
    return []


def spotify_candidate(track):
    return {
        "id": track.get("uri"),
        "attributes": {
            "name": track.get("name"),
            "artistName": ", ".join(
                artist.get("name", "")
                for artist in track.get("artists") or []
                if artist.get("name")
            ),
            "durationInMillis": track.get("duration_ms"),
            "isrc": (track.get("external_ids") or {}).get("isrc"),
        },
    }


def deezer_candidate(track):
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


def _apple_image(attributes):
    artwork = attributes.get("artwork") or {}
    url = artwork.get("url")
    if not url:
        return None
    return url.replace("{w}x{h}", "60x60")


def _label(artists, title):
    return "{} — {}".format(", ".join(artists) or "?", title or "?")


class MusicProvider:
    provider_id = ""
    label = ""
    batch_size = 100
    max_playlist_items = None

    # ------------------------------------------------------------- état
    def is_configured(self):
        raise NotImplementedError

    def is_authenticated(self):
        raise NotImplementedError

    # ------------------------------------------------------------- source
    def list_playlists(self):
        raise NotImplementedError

    def read_playlist(self, playlist_id):
        raise NotImplementedError

    # ---------------------------------------------------------- destination
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


class SpotifyProvider(MusicProvider):
    provider_id = "spotify"
    label = "Spotify"
    max_playlist_items = 10000

    def __init__(self, client):
        self.client = client

    def is_configured(self):
        return bool(self.client.client_id)

    def is_authenticated(self):
        return self.client.is_authenticated()

    def list_playlists(self):
        me = self.client.me()
        my_id = me.get("id")
        playlists = []
        for playlist in self.client.all_playlists():
            owner = playlist.get("owner") or {}
            total = None
            items = playlist.get("items")
            if isinstance(items, dict):
                total = items.get("total")
            if total is None:
                legacy = playlist.get("tracks")
                if isinstance(legacy, dict):
                    total = legacy.get("total")
            owned = owner.get("id") == my_id
            collaborative = bool(playlist.get("collaborative"))
            images = playlist.get("images") or []
            playlists.append({
                "id": playlist.get("id"),
                "name": playlist.get("name") or "(sans titre)",
                "track_count": total,
                "owner": owner.get("display_name") or owner.get("id") or "?",
                "owned": owned,
                "collaborative": collaborative,
                "can_read": owned or collaborative,
                "can_edit": owned or collaborative,
                "image": images[0].get("url") if images else None,
            })
        return playlists

    def read_playlist(self, playlist_id):
        tracks = []
        episodes = 0
        local_files = 0
        unavailable = 0
        for entry in self.client.playlist_items(playlist_id):
            item = entry.get("item") or entry.get("track")
            if not item:
                unavailable += 1
                continue
            if item.get("type") != "track":
                episodes += 1
                continue
            if item.get("is_local") or entry.get("is_local"):
                local_files += 1
                continue
            uri = item.get("uri")
            if not uri:
                unavailable += 1
                continue
            artists = [
                artist.get("name")
                for artist in item.get("artists") or []
                if artist.get("name")
            ]
            isrc = (item.get("external_ids") or {}).get("isrc")
            tracks.append({
                "source_provider": self.provider_id,
                "source_key": uri,
                "direct_id": uri,
                "uri": uri,
                "isrc": isrc,
                "title": item.get("name") or "",
                "artists": artists,
                "album": (item.get("album") or {}).get("name") or "",
                "duration_ms": item.get("duration_ms"),
                "label": _label(artists, item.get("name")),
            })
        return {
            "tracks": tracks,
            "episodes": episodes,
            "local_files": local_files,
            "unavailable": unavailable,
        }

    def existing_tracks(self, playlist_id):
        ids = set()
        isrcs = set()
        for entry in self.client.playlist_items(playlist_id):
            item = entry.get("item") or entry.get("track")
            if not item:
                continue
            uri = item.get("uri")
            if uri:
                ids.add(uri)
            isrc = (item.get("external_ids") or {}).get("isrc")
            if isrc:
                isrcs.add(str(isrc).upper())
        return {"ids": ids, "isrcs": isrcs}

    def resolve_track(self, track):
        if track.get("source_provider") == self.provider_id and track.get("direct_id"):
            return track["direct_id"], "direct"

        isrc = (track.get("isrc") or "").strip()
        if isrc:
            found = self.client.search_track_by_isrc(isrc)
            if found and found.get("uri"):
                return found["uri"], "isrc"

        term = "{} {}".format(
            track.get("title") or "",
            (track.get("artists") or [""])[0],
        ).strip()
        if not term:
            return None, None
        candidates = [
            spotify_candidate(result)
            for result in self.client.search_tracks(term, limit=10)
        ]
        song, _score = pick_best_candidate(track, candidates)
        if song and song.get("id"):
            return song["id"], "search"
        return None, None

    def create_playlist(self, name, description="", public=False):
        playlist = self.client.create_playlist(name, public, description)
        return {"id": playlist.get("id"), "name": playlist.get("name") or name}

    def add_tracks(self, playlist_id, track_ids):
        return self.client.add_items(playlist_id, track_ids)

    def playlist_url(self, playlist_id):
        return "https://open.spotify.com/playlist/{}".format(playlist_id)


class AppleMusicProvider(MusicProvider):
    provider_id = "apple_music"
    label = "Apple Music"

    def __init__(self, client):
        self.client = client

    def is_configured(self):
        return self.client.is_configured()

    def is_authenticated(self):
        return self.client.is_authenticated()

    def list_playlists(self):
        playlists = []
        for playlist in self.client.all_playlists():
            attributes = playlist.get("attributes") or {}
            playlists.append({
                "id": playlist.get("id"),
                "name": attributes.get("name") or "(sans titre)",
                "track_count": attributes.get("trackCount"),
                "can_read": True,
                "can_edit": attributes.get("canEdit", True),
                "image": _apple_image(attributes),
            })
        return playlists

    def read_playlist(self, playlist_id):
        tracks = []
        unavailable = 0
        for item in self.client.playlist_tracks(playlist_id):
            if item.get("type") and item.get("type") not in ("songs", "library-songs"):
                unavailable += 1
                continue
            attributes = item.get("attributes") or {}
            play_params = attributes.get("playParams") or {}
            catalog_id = play_params.get("catalogId") or attributes.get("catalogId")
            if not catalog_id and item.get("type") == "songs":
                catalog_id = item.get("id")
            source_id = catalog_id or item.get("id")
            title = attributes.get("name")
            if not source_id or not title:
                unavailable += 1
                continue
            artists = _artist_names(attributes.get("artistName"))
            tracks.append({
                "source_provider": self.provider_id,
                "source_key": str(source_id),
                "direct_id": str(catalog_id) if catalog_id else None,
                "isrc": attributes.get("isrc"),
                "title": title,
                "artists": artists,
                "album": attributes.get("albumName") or "",
                "duration_ms": attributes.get("durationInMillis"),
                "label": _label(artists, title),
            })
        return {
            "tracks": tracks,
            "episodes": 0,
            "local_files": 0,
            "unavailable": unavailable,
        }

    def existing_tracks(self, playlist_id):
        data = self.client.playlist_existing_tracks(playlist_id)
        return {
            "ids": data.get("catalog_ids", set()),
            "isrcs": data.get("isrcs", set()),
        }

    def resolve_track(self, track):
        if track.get("source_provider") == self.provider_id and track.get("direct_id"):
            return track["direct_id"], "direct"

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


class DeezerProvider(MusicProvider):
    provider_id = "deezer"
    label = "Deezer"

    def __init__(self, client):
        self.client = client

    def is_configured(self):
        return self.client.is_configured()

    def is_authenticated(self):
        return self.client.is_authenticated()

    def list_playlists(self):
        playlists = []
        for playlist in self.client.all_playlists():
            playlists.append({
                "id": str(playlist.get("id")),
                "name": playlist.get("title") or "(sans titre)",
                "track_count": playlist.get("nb_tracks"),
                "can_read": True,
                "can_edit": True,
                "image": playlist.get("picture_medium") or playlist.get("picture_small"),
            })
        return playlists

    def read_playlist(self, playlist_id):
        tracks = []
        unavailable = 0
        for item in self.client.playlist_tracks(playlist_id):
            track_id = item.get("id")
            title = item.get("title")
            if not track_id or not title:
                unavailable += 1
                continue
            artists = _artist_names(item.get("artist"))
            duration = item.get("duration")
            tracks.append({
                "source_provider": self.provider_id,
                "source_key": str(track_id),
                "direct_id": str(track_id),
                "isrc": item.get("isrc"),
                "title": title,
                "artists": artists,
                "album": (item.get("album") or {}).get("title") or "",
                "duration_ms": int(duration) * 1000 if duration else None,
                "label": _label(artists, title),
            })
        return {
            "tracks": tracks,
            "episodes": 0,
            "local_files": 0,
            "unavailable": unavailable,
        }

    def existing_tracks(self, playlist_id):
        return self.client.playlist_existing_tracks(playlist_id)

    def resolve_track(self, track):
        if track.get("source_provider") == self.provider_id and track.get("direct_id"):
            return track["direct_id"], "direct"

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


class YouTubeMusicProvider(MusicProvider):
    provider_id = "youtube_music"
    label = "YouTube Music"

    def __init__(self, client):
        self.client = client

    def is_configured(self):
        return self.client.is_configured()

    def is_authenticated(self):
        return self.client.is_authenticated()

    def list_playlists(self):
        playlists = []
        for playlist in self.client.all_playlists():
            thumbnails = playlist.get("thumbnails") or []
            playlists.append({
                "id": playlist.get("playlistId"),
                "name": playlist.get("title") or "(sans titre)",
                "track_count": playlist.get("count"),
                "can_read": True,
                "can_edit": True,
                "image": thumbnails[-1].get("url") if thumbnails else None,
            })
        return playlists

    def read_playlist(self, playlist_id):
        tracks = []
        unavailable = 0
        for item in self.client.playlist_tracks(playlist_id):
            video_id = item.get("videoId")
            title = item.get("title")
            if not video_id or not title:
                unavailable += 1
                continue
            artists = _artist_names(item.get("artists"))
            duration = item.get("duration_seconds")
            tracks.append({
                "source_provider": self.provider_id,
                "source_key": str(video_id),
                "direct_id": str(video_id),
                "isrc": None,
                "title": title,
                "artists": artists,
                "album": (item.get("album") or {}).get("name") or "",
                "duration_ms": int(duration) * 1000 if duration else None,
                "label": _label(artists, title),
            })
        return {
            "tracks": tracks,
            "episodes": 0,
            "local_files": 0,
            "unavailable": unavailable,
        }

    def existing_tracks(self, playlist_id):
        return self.client.playlist_existing_tracks(playlist_id)

    def resolve_track(self, track):
        if track.get("source_provider") == self.provider_id and track.get("direct_id"):
            return track["direct_id"], "direct"

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
