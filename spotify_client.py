import base64
import hashlib
import json
import os
import secrets
import threading
import time
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

SCOPES = [
    "user-read-private",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
]

MIN_REQUEST_INTERVAL = 0.15
TOKEN_EXPIRY_MARGIN = 60


class SpotifyAuthError(Exception):
    pass


class SpotifyAPIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def generate_pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(client_id, redirect_uri, state, code_challenge):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": " ".join(SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return "{}?{}".format(AUTH_URL, urlencode(params))


def load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_json_file(path, data, mode=0o600):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)


class SpotifyClient:
    def __init__(self, client_id, tokens_path):
        self.client_id = client_id
        self.tokens_path = tokens_path
        self._tokens = load_json_file(tokens_path) or {}
        self._token_lock = threading.Lock()
        self._last_request_at = 0.0
        self.session = requests.Session()

    def is_authenticated(self):
        return bool(self._tokens.get("refresh_token"))

    def scope(self):
        return self._tokens.get("scope", "")

    def _store_tokens(self, payload):
        tokens = dict(self._tokens)
        tokens["access_token"] = payload.get("access_token")
        tokens["expires_at"] = time.time() + int(payload.get("expires_in", 3600))
        if payload.get("scope"):
            tokens["scope"] = payload["scope"]
        if payload.get("refresh_token"):
            tokens["refresh_token"] = payload["refresh_token"]
        self._tokens = tokens
        save_json_file(self.tokens_path, tokens)

    @staticmethod
    def _auth_error_message(resp):
        try:
            body = resp.json()
            return body.get("error_description") or body.get("error") or "Erreur d'authentification"
        except ValueError:
            return "Erreur d'authentification (HTTP {})".format(resp.status_code)

    def exchange_code(self, code, redirect_uri, code_verifier):
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "code_verifier": code_verifier,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise SpotifyAuthError(self._auth_error_message(resp))
        self._store_tokens(resp.json())

    def _refresh(self):
        refresh_token = self._tokens.get("refresh_token")
        if not refresh_token:
            raise SpotifyAuthError("Jeton de session absent ou expiré. Reconnectez-vous à Spotify.")
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise SpotifyAuthError("Impossible de rafraîchir la session. Reconnectez-vous à Spotify.")
        self._store_tokens(resp.json())

    def access_token(self):
        with self._token_lock:
            expires_at = self._tokens.get("expires_at", 0)
            if not self._tokens.get("access_token") or time.time() >= expires_at - TOKEN_EXPIRY_MARGIN:
                self._refresh()
            return self._tokens["access_token"]

    def logout(self):
        self._tokens = {}
        try:
            os.remove(self.tokens_path)
        except OSError:
            pass

    def _throttle(self):
        elapsed = time.time() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_at = time.time()

    @staticmethod
    def _api_error_message(resp):
        try:
            body = resp.json()
            err = body.get("error")
            if isinstance(err, dict):
                return err.get("message") or str(err)
            if err:
                return str(err)
            return str(body)
        except ValueError:
            return "Erreur API Spotify (HTTP {})".format(resp.status_code)

    def request(self, method, path, params=None, json_body=None):
        url = path if path.startswith("http") else API_BASE + path
        attempts = 0
        refreshed = False
        while True:
            self._throttle()
            headers = {"Authorization": "Bearer " + self.access_token()}
            resp = self.session.request(method, url, params=params, json=json_body, headers=headers, timeout=30)
            if resp.status_code == 429 and attempts < 6:
                try:
                    delay = float(resp.headers.get("Retry-After", "2"))
                except ValueError:
                    delay = 2.0
                time.sleep(min(max(delay, 1.0), 30.0))
                attempts += 1
                continue
            if resp.status_code >= 500 and attempts < 3:
                time.sleep(2.0 * (attempts + 1))
                attempts += 1
                continue
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                with self._token_lock:
                    self._refresh()
                continue
            break
        if resp.status_code >= 400:
            raise SpotifyAPIError(resp.status_code, self._api_error_message(resp))
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, json_body=None):
        return self.request("POST", path, json_body=json_body)

    def me(self):
        return self.get("/me")

    def iter_paginated(self, path, params=None):
        data = self.get(path, params=params)
        while True:
            for entry in data.get("items", []):
                yield entry
            next_url = data.get("next")
            if not next_url:
                break
            data = self.get(next_url)

    def all_playlists(self):
        return list(self.iter_paginated("/me/playlists", params={"limit": 50}))

    def playlist_items(self, playlist_id):
        return self.iter_paginated("/playlists/{}/items".format(playlist_id), params={"limit": 100})

    def get_playlist(self, playlist_id):
        return self.get("/playlists/{}".format(playlist_id))

    def create_playlist(self, name, public, description=""):
        return self.post(
            "/me/playlists",
            json_body={"name": name, "public": public, "description": description},
        )

    def add_items(self, playlist_id, uris):
        return self.post("/playlists/{}/items".format(playlist_id), json_body={"uris": uris})

    def playlist_item_uris(self, playlist_id):
        uris = set()
        for entry in self.playlist_items(playlist_id):
            item = entry.get("item") or entry.get("track")
            if item and item.get("uri"):
                uris.add(item["uri"])
        return uris

    def search_track_by_isrc(self, isrc):
        data = self.get(
            "/search",
            params={"q": "isrc:{}".format(isrc), "type": "track", "limit": 10},
        )
        tracks = (data.get("tracks") or {}).get("items") or []
        wanted = str(isrc).upper()
        for track in tracks:
            candidate = ((track.get("external_ids") or {}).get("isrc") or "").upper()
            if candidate == wanted:
                return track
        return tracks[0] if tracks else None

    def search_tracks(self, term, limit=10):
        data = self.get(
            "/search",
            params={"q": term, "type": "track", "limit": limit},
        )
        return (data.get("tracks") or {}).get("items") or []
