"""Client Deezer (API publique + OAuth).

Deezer utilise une authentification OAuth 2.0 côté serveur : l'utilisateur
autorise l'application, qui échange ensuite le code contre un jeton d'accès
de longue durée. Le jeton est stocké localement.
"""

import os
import time
from urllib.parse import parse_qs, urlencode

import requests

from spotify_client import load_json_file, save_json_file

AUTH_URL = "https://connect.deezer.com/oauth/auth.php"
TOKEN_URL = "https://connect.deezer.com/oauth/access_token.php"
API_BASE = "https://api.deezer.com"
SCOPES = "basic_access,manage_library,offline_access"
MIN_REQUEST_INTERVAL = 0.15


class DeezerError(Exception):
    pass


class DeezerConfigError(DeezerError):
    pass


class DeezerAuthError(DeezerError):
    pass


class DeezerAPIError(DeezerError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def build_authorize_url(app_id, redirect_uri, state):
    params = {
        "app_id": app_id,
        "redirect_uri": redirect_uri,
        "perms": SCOPES,
        "state": state,
    }
    return "{}?{}".format(AUTH_URL, urlencode(params))


class DeezerClient:
    def __init__(self, config, tokens_path):
        deezer_config = config.get("deezer") or {}
        self.app_id = str(deezer_config.get("app_id") or "").strip()
        self.app_secret = str(deezer_config.get("app_secret") or "").strip()
        self.tokens_path = tokens_path
        self._tokens = load_json_file(tokens_path) or {}
        self._last_request_at = 0.0
        self.session = requests.Session()

    # ------------------------------------------------------------------ état
    def is_configured(self):
        return bool(self.app_id and self.app_secret)

    def is_authenticated(self):
        return bool(self._tokens.get("access_token"))

    def access_token(self):
        return self._tokens.get("access_token") or ""

    def logout(self):
        self._tokens = {}
        try:
            os.remove(self.tokens_path)
        except OSError:
            pass

    # ---------------------------------------------------------------- OAuth
    def exchange_code(self, code, redirect_uri):
        if not self.is_configured():
            raise DeezerConfigError(
                "Application Deezer non configurée. Renseignez l'App ID et l'App Secret."
            )
        response = self.session.get(
            TOKEN_URL,
            params={
                "app_id": self.app_id,
                "secret": self.app_secret,
                "code": code,
                "output": "json",
            },
            timeout=30,
        )
        data = None
        try:
            data = response.json()
        except ValueError:
            parsed = parse_qs(response.text.strip())
            data = {key: values[0] for key, values in parsed.items() if values}

        if not isinstance(data, dict) or not data.get("access_token"):
            error = data.get("error") if isinstance(data, dict) else response.text
            if isinstance(error, dict):
                error = error.get("message") or error.get("type")
            raise DeezerAuthError(
                "Connexion Deezer impossible : {}".format(error or "réponse invalide")
            )
        self._tokens = {
            "access_token": data["access_token"],
            "expires": data.get("expires", 0),
        }
        save_json_file(self.tokens_path, self._tokens)

    # ------------------------------------------------------------- requêtes
    def _throttle(self):
        elapsed = time.time() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_at = time.time()

    @staticmethod
    def _error_message(response):
        try:
            body = response.json()
        except ValueError:
            return "Erreur Deezer (HTTP {})".format(response.status_code)
        error = body.get("error") or {}
        if isinstance(error, dict):
            return error.get("message") or error.get("type") or str(error)
        return str(error) or "Erreur Deezer (HTTP {})".format(response.status_code)

    def request(self, method, path, params=None, user=True):
        url = path if path.startswith("http") else API_BASE + path
        if user and not self.is_authenticated():
            raise DeezerAuthError(
                "Compte Deezer non connecté. Cliquez sur « Se connecter à Deezer »."
            )

        query = dict(params or {})
        if user:
            query["access_token"] = self.access_token()

        attempts = 0
        while True:
            self._throttle()
            response = self.session.request(
                method, url, params=query, timeout=30
            )
            if response.status_code == 429 and attempts < 6:
                try:
                    delay = float(response.headers.get("Retry-After", "2"))
                except ValueError:
                    delay = 2.0
                time.sleep(min(max(delay, 1.0), 30.0))
                attempts += 1
                continue
            if response.status_code >= 500 and attempts < 3:
                time.sleep(2.0 * (attempts + 1))
                attempts += 1
                continue
            break

        if response.status_code == 401:
            raise DeezerAuthError(
                "Session Deezer expirée ou invalide. Reconnectez-vous à Deezer."
            )
        if response.status_code >= 400:
            raise DeezerAPIError(response.status_code, self._error_message(response))

        try:
            data = response.json()
        except ValueError:
            raise DeezerAPIError(
                response.status_code, "Réponse Deezer illisible."
            )

        error = data.get("error") if isinstance(data, dict) else None
        if error:
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message") or error.get("type") or "Erreur Deezer"
                error_type = error.get("type")
            else:
                code = response.status_code
                message = str(error)
                error_type = None
            if error_type == "OAuthException" or code in (4, 300):
                raise DeezerAuthError(
                    "Session Deezer expirée ou invalide. Reconnectez-vous à Deezer."
                )
            raise DeezerAPIError(code or response.status_code, message)
        return data

    def _iter_collection(self, path, params=None):
        data = self.request("GET", path, params=params)
        while True:
            for item in data.get("data") or []:
                yield item
            next_url = data.get("next")
            if not next_url:
                break
            data = self.request("GET", next_url)

    # -------------------------------------------------------- bibliothèque
    def me(self):
        return self.request("GET", "/user/me")

    def all_playlists(self):
        return [
            playlist
            for playlist in self._iter_collection(
                "/user/me/playlists", params={"limit": 100}
            )
            if playlist.get("id")
        ]

    def playlist_tracks(self, playlist_id):
        return list(
            self._iter_collection(
                "/playlist/{}/tracks".format(playlist_id), params={"limit": 100}
            )
        )

    def playlist_existing_tracks(self, playlist_id):
        ids = set()
        isrcs = set()
        for track in self.playlist_tracks(playlist_id):
            if track.get("id"):
                ids.add(str(track["id"]))
            isrc = track.get("isrc")
            if isrc:
                isrcs.add(str(isrc).upper())
        return {"ids": ids, "isrcs": isrcs}

    # ------------------------------------------------------------- catalogue
    def search_track_by_isrc(self, isrc):
        try:
            data = self.request("GET", "/track/isrc:{}".format(isrc), user=False)
        except DeezerAPIError as exc:
            if exc.status == 800:
                return None
            raise
        return data if isinstance(data, dict) and data.get("id") else None

    def search_tracks(self, term, limit=10):
        data = self.request(
            "GET", "/search", params={"q": term, "limit": limit}, user=False
        )
        return data.get("data") or []

    # -------------------------------------------------------------- écriture
    def create_playlist(self, name, description=""):
        params = {"title": name}
        if description:
            params["description"] = description
        return self.request("POST", "/user/me/playlists", params=params)

    def add_tracks(self, playlist_id, track_ids):
        result = self.request(
            "POST",
            "/playlist/{}/tracks".format(playlist_id),
            params={"songs": ",".join(str(track_id) for track_id in track_ids)},
        )
        if isinstance(result, dict) and result.get("result") is False:
            raise DeezerAPIError(
                502, "Deezer a refusé l'ajout des morceaux à la playlist."
            )
        return result

    def playlist_url(self, playlist_id):
        return "https://www.deezer.com/playlist/{}".format(playlist_id)
