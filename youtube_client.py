"""Client YouTube Music basé sur ytmusicapi (API non officielle).

YouTube Music n'expose pas d'API publique de gestion de playlists. Ce client
s'appuie sur ``ytmusicapi`` et sur l'authentification OAuth « TV et appareils
à accès limité » de Google. Le jeton est stocké localement et rafraîchi
automatiquement par la bibliothèque.
"""

import os
import time

from ytmusicapi import OAuthCredentials, YTMusic
from ytmusicapi.exceptions import YTMusicError

from spotify_client import load_json_file

MIN_REQUEST_INTERVAL = 0.2


class YouTubeMusicError(Exception):
    pass


class YouTubeMusicConfigError(YouTubeMusicError):
    pass


class YouTubeMusicAuthError(YouTubeMusicError):
    pass


class YouTubeMusicAPIError(YouTubeMusicError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class YouTubeMusicClient:
    def __init__(self, config, token_path):
        youtube_config = config.get("youtube_music") or {}
        self.client_id = str(youtube_config.get("client_id") or "").strip()
        self.client_secret = str(youtube_config.get("client_secret") or "").strip()
        self.token_path = token_path
        self._client_instance = None
        self._last_request_at = 0.0

    def is_configured(self):
        return bool(self.client_id and self.client_secret)

    def is_authenticated(self):
        return bool(self.is_configured() and load_json_file(self.token_path))

    def logout(self):
        self._client_instance = None
        try:
            os.remove(self.token_path)
        except OSError:
            pass

    def _throttle(self):
        elapsed = time.time() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_at = time.time()

    def _client(self):
        if self._client_instance is not None:
            return self._client_instance
        if not self.is_configured():
            raise YouTubeMusicConfigError(
                "YouTube Music n'est pas configuré. Renseignez le Client ID et le Client Secret Google."
            )
        if not self.is_authenticated():
            raise YouTubeMusicAuthError(
                "Compte YouTube Music non connecté. Cliquez sur « Connecter YouTube Music »."
            )
        try:
            self._client_instance = YTMusic(
                self.token_path,
                oauth_credentials=OAuthCredentials(
                    self.client_id, self.client_secret
                ),
            )
        except Exception as exc:  # noqa: BLE001
            raise YouTubeMusicAuthError(
                "Impossible d'ouvrir la session YouTube Music : {}".format(exc)
            )
        return self._client_instance

    def _run(self, method, *args, **kwargs):
        self._throttle()
        try:
            return method(*args, **kwargs)
        except YTMusicError as exc:
            message = str(exc) or "Erreur YouTube Music"
            lowered = message.lower()
            if "401" in message or "403" in message or "unauthorized" in lowered or "authenticat" in lowered:
                raise YouTubeMusicAuthError(
                    "Session YouTube Music expirée ou invalide. Reconnectez-vous."
                )
            raise YouTubeMusicAPIError(502, message)
        except Exception as exc:  # noqa: BLE001
            raise YouTubeMusicAPIError(502, str(exc) or "Erreur YouTube Music")

    # -------------------------------------------------------- bibliothèque
    def all_playlists(self):
        playlists = self._run(
            self._client().get_library_playlists, limit=None
        ) or []
        if not isinstance(playlists, list):
            raise YouTubeMusicAPIError(
                502, "Réponse inattendue de YouTube Music pour les playlists."
            )
        return [playlist for playlist in playlists if playlist.get("playlistId")]

    def playlist_tracks(self, playlist_id):
        data = self._run(
            self._client().get_playlist, playlist_id, limit=None
        ) or {}
        if not isinstance(data, dict):
            raise YouTubeMusicAPIError(
                502, "Réponse inattendue de YouTube Music pour la playlist."
            )
        if data.get("error"):
            raise YouTubeMusicAPIError(502, str(data["error"]))
        return [
            track
            for track in data.get("tracks") or []
            if track and track.get("videoId")
        ]

    def playlist_existing_tracks(self, playlist_id):
        return {
            "ids": {str(track["videoId"]) for track in self.playlist_tracks(playlist_id)},
            "isrcs": set(),
        }

    # ------------------------------------------------------------- catalogue
    def search_tracks(self, term, limit=10):
        results = self._run(
            self._client().search,
            term,
            filter="songs",
            limit=limit,
        ) or []
        if not isinstance(results, list):
            raise YouTubeMusicAPIError(
                502, "Réponse inattendue de YouTube Music pour la recherche."
            )
        return [result for result in results if result.get("videoId")]

    # -------------------------------------------------------------- écriture
    def create_playlist(self, name, description="", public=False):
        result = self._run(
            self._client().create_playlist,
            name,
            description or "",
            privacy_status="PUBLIC" if public else "PRIVATE",
        )
        playlist_id = result
        if isinstance(result, dict):
            playlist_id = result.get("playlistId") or result.get("id")
        if not playlist_id:
            raise YouTubeMusicAPIError(
                502, "YouTube Music n'a pas renvoyé l'identifiant de la playlist créée."
            )
        return {"id": playlist_id, "attributes": {"name": name}}

    def add_tracks(self, playlist_id, track_ids):
        result = self._run(
            self._client().add_playlist_items,
            playlist_id,
            list(track_ids),
            duplicates=False,
        )
        if isinstance(result, dict) and result.get("error"):
            raise YouTubeMusicAPIError(502, str(result["error"]))
        if isinstance(result, str) and "FAILED" in result.upper():
            raise YouTubeMusicAPIError(502, result)
        return result

    def playlist_url(self, playlist_id):
        return "https://music.youtube.com/playlist?list={}".format(playlist_id)


def start_device_authorization(client_id, client_secret):
    try:
        return OAuthCredentials(client_id, client_secret).get_code()
    except Exception as exc:  # noqa: BLE001
        raise YouTubeMusicConfigError(
            "Impossible de démarrer l'autorisation Google : {}".format(exc)
        )


def poll_device_authorization(client_id, client_secret, device_code):
    try:
        return OAuthCredentials(client_id, client_secret).token_from_code(device_code)
    except Exception as exc:  # noqa: BLE001
        raise YouTubeMusicAPIError(
            502, "Erreur d'autorisation Google : {}".format(exc)
        )
