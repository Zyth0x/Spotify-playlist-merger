"""Client Apple Music (MusicKit / Apple Music API).

L'authentification se fait en deux temps :

1. le serveur signe un jeton développeur (JWT ES256) avec la clé privée .p8
   fournie par l'utilisateur ;
2. MusicKit JS, chargé dans le navigateur, échange ce jeton contre un
   « Music User Token » lors de la connexion du compte Apple Music.

Le jeton utilisateur est ensuite envoyé à l'API Apple Music avec le jeton
développeur pour lire la bibliothèque et créer des playlists.
"""

import os
import time

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from spotify_client import load_json_file, save_json_file

API_BASE = "https://api.music.apple.com/v1"
DEVELOPER_TOKEN_TTL = 15_777_000  # 6 mois, durée maximale conseillée par Apple
MIN_REQUEST_INTERVAL = 0.25


class AppleMusicError(Exception):
    pass


class AppleMusicConfigError(AppleMusicError):
    pass


class AppleMusicAuthError(AppleMusicError):
    pass


class AppleMusicAPIError(AppleMusicError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def validate_private_key(private_key):
    """Retourne un message d'erreur, ou None si la clé .p8 est utilisable."""
    try:
        key = load_pem_private_key(private_key.encode("utf-8"), password=None)
    except Exception as exc:  # noqa: BLE001 - message destiné à l'utilisateur
        return "Clé privée illisible : {}".format(exc)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        return "La clé doit être une clé elliptique EC au format .p8."
    if not isinstance(key.curve, ec.SECP256R1):
        return "La clé doit utiliser la courbe P-256."
    return None


def make_developer_token(team_id, key_id, private_key, origin=None):
    now = int(time.time())
    payload = {
        "iss": team_id,
        "iat": now,
        "exp": now + DEVELOPER_TOKEN_TTL,
    }
    if origin:
        payload["origin"] = [origin] if isinstance(origin, str) else list(origin)
    try:
        return jwt.encode(
            payload,
            private_key,
            algorithm="ES256",
            headers={"alg": "ES256", "kid": key_id},
        )
    except Exception as exc:  # noqa: BLE001
        raise AppleMusicConfigError("Impossible de signer le jeton Apple Music : {}".format(exc))


class AppleMusicClient:
    def __init__(self, config, key_path, tokens_path, origin=None):
        apple_config = config.get("apple_music") or {}
        self.team_id = (apple_config.get("team_id") or "").strip().upper()
        self.key_id = (apple_config.get("key_id") or "").strip().upper()
        self.storefront = (apple_config.get("storefront") or "fr").strip().lower()
        self.key_path = key_path
        self.tokens_path = tokens_path
        self.origin = origin
        self._tokens = load_json_file(tokens_path) or {}
        self._developer_tokens = {}
        self._last_request_at = 0.0
        self.session = requests.Session()

    # ------------------------------------------------------------------ état
    def _private_key(self):
        try:
            with open(self.key_path, "r", encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def is_configured(self):
        return bool(self.team_id and self.key_id and self._private_key())

    def is_authenticated(self):
        return bool((self._tokens or {}).get("music_user_token"))

    def developer_token(self, for_browser=False):
        """Signe un jeton développeur.

        Le jeton destiné à MusicKit JS porte une restriction ``origin``. Le
        jeton utilisé par le serveur pour appeler l'API Apple Music n'en porte
        pas, car les requêtes serveur n'envoient pas d'en-tête ``Origin``.
        """
        now = time.time()
        cache_key = "browser" if for_browser else "server"
        cached = self._developer_tokens.get(cache_key)
        if cached and now < cached[1] - 3600:
            return cached[0]

        private_key = self._private_key()
        if not private_key:
            raise AppleMusicConfigError(
                "Clé privée Apple Music manquante. Configurez votre clé .p8."
            )
        if not self.team_id or not self.key_id:
            raise AppleMusicConfigError(
                "Team ID ou Key ID Apple Music manquant. Reconfigurez Apple Music."
            )
        token = make_developer_token(
            self.team_id,
            self.key_id,
            private_key,
            origin=self.origin if for_browser else None,
        )
        self._developer_tokens[cache_key] = (token, now + DEVELOPER_TOKEN_TTL)
        return token

    def set_user_token(self, music_user_token):
        self._tokens = {"music_user_token": music_user_token}
        save_json_file(self.tokens_path, self._tokens)

    def logout(self):
        self._tokens = {}
        try:
            os.remove(self.tokens_path)
        except OSError:
            pass

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
            return "Erreur Apple Music (HTTP {})".format(response.status_code)
        errors = body.get("errors") or []
        if errors:
            first = errors[0] or {}
            parts = [first.get("title"), first.get("detail")]
            message = " — ".join(part for part in parts if part)
            if message:
                return message
        return body.get("error") or "Erreur Apple Music (HTTP {})".format(
            response.status_code
        )

    def request(self, method, path, params=None, json_body=None, user=True):
        if path.startswith("http"):
            url = path
        elif path.startswith("/v1/"):
            url = "https://api.music.apple.com" + path
        else:
            url = API_BASE + path

        if user and not self.is_authenticated():
            raise AppleMusicAuthError(
                "Compte Apple Music non connecté. Cliquez sur « Se connecter à Apple Music »."
            )

        attempts = 0
        while True:
            self._throttle()
            headers = {"Authorization": "Bearer " + self.developer_token()}
            if user:
                headers["Music-User-Token"] = self._tokens.get("music_user_token", "")
            response = self.session.request(
                method, url, params=params, json=json_body, headers=headers, timeout=30
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
            if user:
                raise AppleMusicAuthError(
                    "Session Apple Music expirée ou invalide. Reconnectez-vous à Apple Music."
                )
            raise AppleMusicConfigError(
                "Jeton développeur Apple Music refusé. Vérifiez le Team ID, le Key ID et la clé .p8."
            )
        if response.status_code == 403:
            raise AppleMusicAPIError(
                403,
                self._error_message(response)
                + " Vérifiez que votre abonnement Apple Music est actif.",
            )
        if response.status_code >= 400:
            raise AppleMusicAPIError(response.status_code, self._error_message(response))
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def _iter_collection(self, path, params=None):
        data = self.request("GET", path, params=params)
        while True:
            for item in data.get("data") or []:
                yield item
            next_path = data.get("next")
            if not next_path:
                break
            data = self.request("GET", next_path)

    # -------------------------------------------------------- bibliothèque
    def me_storefront(self):
        data = self.request("GET", "/me/storefront")
        rows = data.get("data") or []
        return rows[0].get("id") if rows else self.storefront

    def all_playlists(self):
        return list(
            self._iter_collection("/me/library/playlists", params={"limit": 100})
        )

    def playlist_tracks(self, playlist_id):
        return list(
            self._iter_collection(
                "/me/library/playlists/{}/tracks".format(playlist_id),
                params={"limit": 100},
            )
        )

    def playlist_existing_tracks(self, playlist_id):
        """Identifiants catalogue et ISRC déjà présents dans une playlist."""
        catalog_ids = set()
        isrcs = set()
        for item in self.playlist_tracks(playlist_id):
            attributes = item.get("attributes") or {}
            play_params = attributes.get("playParams") or {}
            catalog_id = play_params.get("catalogId") or attributes.get("catalogId")
            if catalog_id:
                catalog_ids.add(str(catalog_id))
            elif item.get("type") == "songs" and item.get("id"):
                catalog_ids.add(str(item["id"]))
            isrc = attributes.get("isrc")
            if isrc:
                isrcs.add(str(isrc).upper())
        return {"catalog_ids": catalog_ids, "isrcs": isrcs}

    # ------------------------------------------------------------- catalogue
    def search_song_by_isrc(self, isrc):
        data = self.request(
            "GET",
            "/catalog/{}/songs".format(self.storefront),
            params={"filter[isrc]": isrc},
            user=False,
        )
        songs = data.get("data") or []
        wanted = str(isrc).upper()
        for song in songs:
            candidate_isrc = (song.get("attributes") or {}).get("isrc")
            if candidate_isrc and str(candidate_isrc).upper() == wanted:
                return song
        return songs[0] if songs else None

    def search_songs(self, term, limit=10):
        data = self.request(
            "GET",
            "/catalog/{}/search".format(self.storefront),
            params={"term": term, "types": "songs", "limit": limit},
            user=False,
        )
        results = data.get("results") or {}
        songs = results.get("songs") or {}
        return songs.get("data") or []

    # -------------------------------------------------------------- écriture
    def create_playlist(self, name, description="", public=False, song_ids=None):
        song_ids = list(song_ids or [])
        body = {
            "attributes": {"name": name},
            "relationships": {
                "tracks": {
                    "data": [
                        {"id": str(song_id), "type": "songs"}
                        for song_id in song_ids
                    ]
                }
            },
        }
        if description:
            body["attributes"]["description"] = description
        if public:
            body["attributes"]["isPublic"] = True
        response = self.request("POST", "/me/library/playlists", json_body=body)
        rows = response.get("data") or []
        return rows[0] if rows else {}

    def add_tracks(self, playlist_id, song_ids):
        body = {
            "data": [
                {"id": str(song_id), "type": "songs"} for song_id in song_ids
            ]
        }
        return self.request(
            "POST",
            "/me/library/playlists/{}/tracks".format(playlist_id),
            json_body=body,
        )
