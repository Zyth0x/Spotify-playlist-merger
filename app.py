import os
import re
import secrets
import threading
import time
import uuid

from flask import Flask, jsonify, redirect, render_template, request, session

from apple_client import (
    AppleMusicAPIError,
    AppleMusicAuthError,
    AppleMusicClient,
    AppleMusicConfigError,
    validate_private_key,
)
from deezer_client import (
    DeezerAPIError,
    DeezerAuthError,
    DeezerClient,
    DeezerConfigError,
    build_authorize_url as build_deezer_authorize_url,
)
from providers import (
    AppleMusicProvider,
    DeezerProvider,
    SpotifyProvider,
    YouTubeMusicProvider,
)
from spotify_client import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyClient,
    build_authorize_url,
    generate_pkce_pair,
    load_json_file,
    save_json_file,
)
from youtube_client import (
    YouTubeMusicAPIError,
    YouTubeMusicAuthError,
    YouTubeMusicClient,
    YouTubeMusicConfigError,
    poll_device_authorization,
    start_device_authorization,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
TOKENS_PATH = os.path.join(DATA_DIR, "tokens.json")
APPLE_KEY_PATH = os.path.join(DATA_DIR, "apple_key.p8")
APPLE_TOKENS_PATH = os.path.join(DATA_DIR, "apple_tokens.json")
DEEZER_TOKENS_PATH = os.path.join(DATA_DIR, "deezer_tokens.json")
YOUTUBE_TOKEN_PATH = os.path.join(DATA_DIR, "ytmusic_oauth.json")

HOST = "127.0.0.1"
PORT = 8888
ORIGIN = "http://{}:{}".format(HOST, PORT)
APPLE_ORIGINS = [ORIGIN, "http://localhost:{}".format(PORT)]
REDIRECT_URI = ORIGIN + "/callback"
DEEZER_REDIRECT_URI = ORIGIN + "/deezer/callback"

DUP_PREVIEW_LIMIT = 50

MUSIC_PROVIDERS = ("spotify", "apple_music", "deezer", "youtube_music")
YOUTUBE_FLOWS = {}
YOUTUBE_FLOWS_LOCK = threading.Lock()
YOUTUBE_FLOW_TTL = 1800


class MergeError(Exception):
    pass


app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

JOBS = {}
JOBS_LOCK = threading.Lock()


def load_config():
    return load_json_file(CONFIG_PATH) or {}


def update_config(updates):
    config = load_config()
    config.update(updates)
    save_json_file(CONFIG_PATH, config)


def get_client():
    client_id = load_config().get("client_id")
    if not client_id:
        raise SpotifyAuthError("Client ID manquant. Configurez d'abord votre application Spotify.")
    return SpotifyClient(client_id, TOKENS_PATH)


def get_apple_client():
    return AppleMusicClient(load_config(), APPLE_KEY_PATH, APPLE_TOKENS_PATH, APPLE_ORIGINS)


def get_deezer_client():
    return DeezerClient(load_config(), DEEZER_TOKENS_PATH)


def get_youtube_client():
    return YouTubeMusicClient(load_config(), YOUTUBE_TOKEN_PATH)


def get_music_provider(provider):
    if provider == "spotify":
        return SpotifyProvider(get_client())
    if provider == "apple_music":
        return AppleMusicProvider(get_apple_client())
    if provider == "deezer":
        return DeezerProvider(get_deezer_client())
    if provider == "youtube_music":
        return YouTubeMusicProvider(get_youtube_client())
    raise ValueError("Fournisseur de musique inconnu : {}".format(provider))


AUTH_ERRORS = (
    SpotifyAuthError,
    AppleMusicAuthError,
    DeezerAuthError,
    YouTubeMusicAuthError,
)
CONFIG_ERRORS = (
    SpotifyAuthError,
    AppleMusicConfigError,
    DeezerConfigError,
    YouTubeMusicConfigError,
)
API_ERRORS = (
    SpotifyAPIError,
    AppleMusicAPIError,
    DeezerAPIError,
    YouTubeMusicAPIError,
)


def provider_error_response(exc):
    if isinstance(exc, AUTH_ERRORS):
        return jsonify({"error": str(exc)}), 401
    if isinstance(exc, CONFIG_ERRORS):
        return jsonify({"error": str(exc)}), 400
    if isinstance(exc, API_ERRORS):
        return jsonify({"error": getattr(exc, "message", str(exc))}), 502
    raise exc


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def apple_public_state(client):
    return {
        "configured": client.is_configured(),
        "authenticated": client.is_authenticated(),
        "storefront": client.storefront,
        "team_id": client.team_id or None,
        "key_id": client.key_id or None,
    }


def deezer_public_state(client):
    return {
        "configured": client.is_configured(),
        "authenticated": client.is_authenticated(),
        "app_id": client.app_id or None,
        "redirect_uri": DEEZER_REDIRECT_URI,
    }


def youtube_public_state(client):
    return {
        "configured": client.is_configured(),
        "authenticated": client.is_authenticated(),
        "client_id": client.client_id or None,
    }


def apple_artwork(attributes):
    artwork = attributes.get("artwork") or {}
    url = artwork.get("url")
    if not url:
        return None
    return url.replace("{w}x{h}", "60x60")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    config = load_config()
    client_id = config.get("client_id")
    authenticated = False
    me = None
    if client_id:
        client = SpotifyClient(client_id, TOKENS_PATH)
        authenticated = client.is_authenticated()
        if authenticated:
            try:
                profile = client.me()
                images = profile.get("images") or []
                me = {
                    "id": profile.get("id"),
                    "display_name": profile.get("display_name") or profile.get("id"),
                    "image": images[0].get("url") if images else None,
                }
            except SpotifyAuthError:
                authenticated = False
            except SpotifyAPIError as exc:
                if exc.status in (401, 403):
                    authenticated = False

    apple = apple_public_state(get_apple_client())
    deezer = deezer_public_state(get_deezer_client())
    youtube = youtube_public_state(get_youtube_client())
    return jsonify({
        "has_client_id": bool(client_id),
        "authenticated": authenticated,
        "me": me,
        "apple": apple,
        "deezer": deezer,
        "youtube_music": youtube,
        "providers": {
            "spotify": {
                "configured": bool(client_id),
                "authenticated": authenticated,
                "me": me,
            },
            "apple_music": apple,
            "deezer": deezer,
            "youtube_music": youtube,
        },
    })


@app.route("/api/client-id", methods=["POST"])
def api_client_id():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    if not client_id or len(client_id) > 64 or " " in client_id:
        return jsonify({"error": "Client ID invalide."}), 400
    update_config({"client_id": client_id})
    return jsonify({"ok": True})


@app.route("/login")
def login():
    client_id = load_config().get("client_id")
    if not client_id:
        return redirect("/")
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(24)
    session["pkce_verifier"] = verifier
    session["oauth_state"] = state
    return redirect(build_authorize_url(client_id, REDIRECT_URI, state, challenge))


@app.route("/callback")
def callback():
    if request.args.get("error"):
        return redirect("/?auth_error=1")
    code = request.args.get("code")
    state = request.args.get("state")
    verifier = session.pop("pkce_verifier", None)
    expected_state = session.pop("oauth_state", None)
    if not code or not verifier or not state or state != expected_state:
        return redirect("/?auth_error=2")
    try:
        get_client().exchange_code(code, REDIRECT_URI, verifier)
    except SpotifyAuthError:
        return redirect("/?auth_error=3")
    return redirect("/?connected=1")


@app.route("/api/logout", methods=["POST"])
def api_logout():
    client_id = load_config().get("client_id")
    if client_id:
        SpotifyClient(client_id, TOKENS_PATH).logout()
    return jsonify({"ok": True})


@app.route("/api/playlists")
def api_playlists():
    provider_id = request.args.get("provider") or "spotify"
    if provider_id not in MUSIC_PROVIDERS:
        return jsonify({"error": "Fournisseur source inconnu."}), 400
    try:
        provider = get_music_provider(provider_id)
        if not provider.is_configured():
            return jsonify({
                "error": "{} n'est pas configuré.".format(provider.label)
            }), 400
        if not provider.is_authenticated():
            return jsonify({
                "error": "Compte {} non connecté.".format(provider.label)
            }), 401
        playlists = provider.list_playlists()
    except Exception as exc:  # noqa: BLE001 - converti en réponse HTTP
        return provider_error_response(exc)
    return jsonify({"provider": provider_id, "playlists": playlists})


# ---------------------------------------------------------------- Apple Music


@app.route("/api/apple/config", methods=["POST"])
def api_apple_config():
    data = request.get_json(silent=True) or {}
    team_id = str(data.get("team_id") or "").strip().upper()
    key_id = str(data.get("key_id") or "").strip().upper()
    storefront = str(data.get("storefront") or "fr").strip().lower()
    private_key = str(data.get("private_key") or "")

    if not re.fullmatch(r"[A-Z0-9]{10}", team_id):
        return jsonify({"error": "Team ID Apple invalide (10 caractères alphanumériques)."}), 400
    if not re.fullmatch(r"[A-Z0-9]{10}", key_id):
        return jsonify({"error": "Key ID Apple invalide (10 caractères alphanumériques)."}), 400
    if not re.fullmatch(r"[a-z]{2}", storefront):
        return jsonify({"error": "Storefront invalide (deux lettres, par exemple « fr »)."}), 400

    if private_key:
        private_key = private_key.replace("\\n", "\n").strip()
        if not private_key.endswith("\n"):
            private_key += "\n"
        error = validate_private_key(private_key)
        if error:
            return jsonify({"error": error}), 400
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp_path = APPLE_KEY_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(private_key)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, APPLE_KEY_PATH)
        except OSError as exc:
            return jsonify({"error": "Impossible d'enregistrer la clé privée : {}".format(exc)}), 500
    elif not os.path.exists(APPLE_KEY_PATH):
        return jsonify({"error": "Collez le contenu de votre clé privée .p8."}), 400

    previous = load_config().get("apple_music") or {}
    update_config({
        "apple_music": {
            "team_id": team_id,
            "key_id": key_id,
            "storefront": storefront,
        }
    })

    # Le jeton utilisateur est lié à l'application MusicKit : on l'oublie si
    # l'identité ou la clé change.
    identity_changed = (
        private_key
        or previous.get("team_id") != team_id
        or previous.get("key_id") != key_id
    )
    if identity_changed:
        try:
            os.remove(APPLE_TOKENS_PATH)
        except OSError:
            pass

    client = get_apple_client()
    try:
        client.developer_token()
    except AppleMusicConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "apple": apple_public_state(client)})


@app.route("/api/apple/developer-token")
def api_apple_developer_token():
    client = get_apple_client()
    if not client.is_configured():
        return jsonify({"error": "Apple Music n'est pas encore configuré."}), 400
    try:
        token = client.developer_token(for_browser=True)
    except AppleMusicConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    response = jsonify({
        "developer_token": token,
        "storefront": client.storefront,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/apple/session", methods=["POST"])
def api_apple_session():
    data = request.get_json(silent=True) or {}
    music_user_token = str(data.get("music_user_token") or "").strip()
    if not music_user_token:
        return jsonify({"error": "Jeton Apple Music manquant."}), 400

    client = get_apple_client()
    if not client.is_configured():
        return jsonify({"error": "Apple Music n'est pas encore configuré."}), 400

    client.set_user_token(music_user_token)
    try:
        client.me_storefront()
    except AppleMusicAuthError as exc:
        client.logout()
        return jsonify({"error": str(exc)}), 401
    except AppleMusicConfigError as exc:
        client.logout()
        return jsonify({"error": str(exc)}), 400
    except AppleMusicAPIError as exc:
        client.logout()
        return jsonify({"error": exc.message}), 400

    return jsonify({"ok": True, "apple": apple_public_state(client)})


@app.route("/api/apple/logout", methods=["POST"])
def api_apple_logout():
    get_apple_client().logout()
    return jsonify({"ok": True})


@app.route("/api/apple/playlists")
def api_apple_playlists():
    client = get_apple_client()
    if not client.is_configured():
        return jsonify({"error": "Apple Music n'est pas encore configuré."}), 400
    if not client.is_authenticated():
        return jsonify({"error": "Compte Apple Music non connecté."}), 401

    try:
        raw_playlists = client.all_playlists()
    except AppleMusicAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except AppleMusicConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    except AppleMusicAPIError as exc:
        return jsonify({"error": exc.message}), 502

    playlists = []
    for pl in raw_playlists:
        attributes = pl.get("attributes") or {}
        playlists.append({
            "id": pl.get("id"),
            "name": attributes.get("name") or "(sans titre)",
            "track_count": attributes.get("trackCount"),
            "can_edit": attributes.get("canEdit", True),
            "image": apple_artwork(attributes),
        })
    return jsonify({"playlists": playlists})


# -------------------------------------------------------------------- Deezer


@app.route("/api/deezer/config", methods=["POST"])
def api_deezer_config():
    data = request.get_json(silent=True) or {}
    app_id = str(data.get("app_id") or "").strip()
    app_secret = str(data.get("app_secret") or "").strip()
    if not app_id or len(app_id) > 64 or " " in app_id:
        return jsonify({"error": "App ID Deezer invalide."}), 400
    if not app_secret or len(app_secret) > 128 or " " in app_secret:
        return jsonify({"error": "App Secret Deezer invalide."}), 400

    previous = load_config().get("deezer") or {}
    update_config({"deezer": {"app_id": app_id, "app_secret": app_secret}})
    if (previous.get("app_id"), previous.get("app_secret")) != (app_id, app_secret):
        try:
            os.remove(DEEZER_TOKENS_PATH)
        except OSError:
            pass
    client = get_deezer_client()
    return jsonify({"ok": True, "deezer": deezer_public_state(client)})


@app.route("/deezer/login")
def deezer_login():
    client = get_deezer_client()
    if not client.is_configured():
        return redirect("/?deezer_error=config")
    state = secrets.token_urlsafe(24)
    session["deezer_oauth_state"] = state
    return redirect(
        build_deezer_authorize_url(client.app_id, DEEZER_REDIRECT_URI, state)
    )


@app.route("/deezer/callback")
def deezer_callback():
    if request.args.get("error"):
        return redirect("/?deezer_error=denied")
    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.pop("deezer_oauth_state", None)
    if not code or (expected_state and state != expected_state):
        return redirect("/?deezer_error=state")
    try:
        get_deezer_client().exchange_code(code, DEEZER_REDIRECT_URI)
    except (DeezerAuthError, DeezerConfigError):
        return redirect("/?deezer_error=auth")
    return redirect("/?deezer_connected=1")


@app.route("/api/deezer/logout", methods=["POST"])
def api_deezer_logout():
    get_deezer_client().logout()
    return jsonify({"ok": True})


@app.route("/api/deezer/playlists")
def api_deezer_playlists():
    client = get_deezer_client()
    if not client.is_configured():
        return jsonify({"error": "Deezer n'est pas encore configuré."}), 400
    if not client.is_authenticated():
        return jsonify({"error": "Compte Deezer non connecté."}), 401
    try:
        raw_playlists = client.all_playlists()
    except DeezerAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except DeezerConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    except DeezerAPIError as exc:
        return jsonify({"error": exc.message}), 502

    playlists = []
    for playlist in raw_playlists:
        playlists.append({
            "id": str(playlist.get("id")),
            "name": playlist.get("title") or "(sans titre)",
            "track_count": playlist.get("nb_tracks"),
            "can_edit": True,
            "image": playlist.get("picture_medium") or playlist.get("picture_small"),
        })
    return jsonify({"playlists": playlists})


# ------------------------------------------------------------- YouTube Music


@app.route("/api/youtube/config", methods=["POST"])
def api_youtube_config():
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("client_id") or "").strip()
    client_secret = str(data.get("client_secret") or "").strip()
    if not client_id or len(client_id) > 256 or " " in client_id:
        return jsonify({"error": "Client ID Google invalide."}), 400
    if not client_secret or len(client_secret) > 256 or " " in client_secret:
        return jsonify({"error": "Client Secret Google invalide."}), 400

    previous = load_config().get("youtube_music") or {}
    update_config({
        "youtube_music": {
            "client_id": client_id,
            "client_secret": client_secret,
        }
    })
    if (previous.get("client_id"), previous.get("client_secret")) != (
        client_id,
        client_secret,
    ):
        try:
            os.remove(YOUTUBE_TOKEN_PATH)
        except OSError:
            pass
    client = get_youtube_client()
    return jsonify({"ok": True, "youtube_music": youtube_public_state(client)})


@app.route("/api/youtube/device", methods=["POST"])
def api_youtube_device():
    client = get_youtube_client()
    if not client.is_configured():
        return jsonify({"error": "YouTube Music n'est pas encore configuré."}), 400
    try:
        code = start_device_authorization(client.client_id, client.client_secret)
    except YouTubeMusicConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    except YouTubeMusicAPIError as exc:
        return jsonify({"error": exc.message}), 502

    flow_id = uuid.uuid4().hex
    now = time.time()
    try:
        interval = max(1, int(code.get("interval", 5)))
    except (TypeError, ValueError):
        interval = 5
    try:
        expires_in = max(60, int(code.get("expires_in", YOUTUBE_FLOW_TTL)))
    except (TypeError, ValueError):
        expires_in = YOUTUBE_FLOW_TTL
    flow = {
        "device_code": code.get("device_code"),
        "interval": interval,
        "expires_at": now + expires_in,
        "client_id": client.client_id,
        "client_secret": client.client_secret,
        "last_poll": 0.0,
    }
    if not flow["device_code"]:
        return jsonify({"error": "Google n'a pas renvoyé de code d'autorisation."}), 502

    with YOUTUBE_FLOWS_LOCK:
        YOUTUBE_FLOWS[flow_id] = flow
        for old_id, old_flow in list(YOUTUBE_FLOWS.items()):
            if old_flow["expires_at"] < now:
                YOUTUBE_FLOWS.pop(old_id, None)

    return jsonify({
        "flow_id": flow_id,
        "user_code": code.get("user_code"),
        "verification_url": code.get("verification_url"),
        "interval": interval,
        "expires_in": expires_in,
    })


@app.route("/api/youtube/device/status")
def api_youtube_device_status():
    flow_id = request.args.get("flow_id") or ""
    with YOUTUBE_FLOWS_LOCK:
        flow = dict(YOUTUBE_FLOWS.get(flow_id) or {})
    if not flow:
        return jsonify({"error": "Autorisation YouTube Music introuvable ou expirée."}), 404

    now = time.time()
    if now >= flow["expires_at"]:
        with YOUTUBE_FLOWS_LOCK:
            YOUTUBE_FLOWS.pop(flow_id, None)
        return jsonify({"status": "expired", "error": "Le code d'autorisation a expiré."}), 400

    with YOUTUBE_FLOWS_LOCK:
        current = YOUTUBE_FLOWS.get(flow_id)
        if current is None:
            return jsonify({"status": "expired", "error": "Autorisation expirée."}), 400
        if now - current["last_poll"] < current["interval"]:
            return jsonify({"status": "pending"})
        current["last_poll"] = now
        interval = current["interval"]

    try:
        data = poll_device_authorization(
            flow["client_id"], flow["client_secret"], flow["device_code"]
        )
    except YouTubeMusicAPIError as exc:
        return jsonify({"status": "error", "error": exc.message}), 502

    error = data.get("error") if isinstance(data, dict) else "invalid_response"
    if error == "authorization_pending":
        return jsonify({"status": "pending"})
    if error == "slow_down":
        with YOUTUBE_FLOWS_LOCK:
            current = YOUTUBE_FLOWS.get(flow_id)
            if current:
                current["interval"] = current.get("interval", interval) + 5
        return jsonify({"status": "pending"})
    if error:
        with YOUTUBE_FLOWS_LOCK:
            YOUTUBE_FLOWS.pop(flow_id, None)
        messages = {
            "access_denied": "Autorisation refusée par l'utilisateur.",
            "expired_token": "Le code d'autorisation a expiré.",
            "invalid_grant": "Code d'autorisation invalide.",
        }
        return jsonify({
            "status": "error",
            "error": messages.get(str(error), str(error)),
        }), 400
    if not data.get("access_token"):
        with YOUTUBE_FLOWS_LOCK:
            YOUTUBE_FLOWS.pop(flow_id, None)
        return jsonify({"status": "error", "error": "Réponse d'autorisation invalide."}), 502

    expires_in = int(data.get("expires_in", 3600))
    token = {
        "scope": data.get("scope", "https://www.googleapis.com/auth/youtube"),
        "token_type": data.get("token_type", "Bearer"),
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": int(time.time()) + expires_in,
        "expires_in": expires_in,
    }
    save_json_file(YOUTUBE_TOKEN_PATH, token)
    with YOUTUBE_FLOWS_LOCK:
        YOUTUBE_FLOWS.pop(flow_id, None)

    client = get_youtube_client()
    return jsonify({"status": "done", "youtube_music": youtube_public_state(client)})


@app.route("/api/youtube/logout", methods=["POST"])
def api_youtube_logout():
    get_youtube_client().logout()
    return jsonify({"ok": True})


@app.route("/api/youtube/playlists")
def api_youtube_playlists():
    client = get_youtube_client()
    if not client.is_configured():
        return jsonify({"error": "YouTube Music n'est pas encore configuré."}), 400
    if not client.is_authenticated():
        return jsonify({"error": "Compte YouTube Music non connecté."}), 401
    try:
        raw_playlists = client.all_playlists()
    except YouTubeMusicAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except YouTubeMusicConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    except YouTubeMusicAPIError as exc:
        return jsonify({"error": exc.message}), 502

    playlists = []
    for playlist in raw_playlists:
        thumbnails = playlist.get("thumbnails") or []
        image = thumbnails[-1].get("url") if thumbnails else None
        playlists.append({
            "id": playlist.get("playlistId"),
            "name": playlist.get("title") or "(sans titre)",
            "track_count": playlist.get("count"),
            "can_edit": True,
            "image": image,
        })
    return jsonify({"playlists": playlists})


# --------------------------------------------------------------------- merge


@app.route("/api/merge", methods=["POST"])
def api_merge():
    data = request.get_json(silent=True) or {}
    playlist_ids = list(dict.fromkeys(data.get("playlist_ids") or []))
    if not playlist_ids:
        return jsonify({"error": "Sélectionnez au moins une playlist source."}), 400

    target = data.get("target") or {}
    provider = target.get("provider") or "spotify"
    if provider not in MUSIC_PROVIDERS:
        return jsonify({"error": "Fournisseur de destination inconnu."}), 400

    source_id = data.get("source") or "spotify"
    if source_id not in MUSIC_PROVIDERS:
        return jsonify({"error": "Fournisseur source inconnu."}), 400

    mode = target.get("mode")
    if mode not in ("new", "existing"):
        return jsonify({"error": "Destination invalide."}), 400
    if mode == "new" and not (target.get("name") or "").strip():
        return jsonify({"error": "Indiquez un nom pour la nouvelle playlist."}), 400
    if mode == "existing" and not target.get("id"):
        return jsonify({"error": "Choisissez une playlist existante."}), 400

    try:
        source = get_music_provider(source_id)
        destination = get_music_provider(provider)
    except Exception as exc:  # noqa: BLE001 - converti en réponse HTTP
        return provider_error_response(exc)

    if not source.is_configured():
        return jsonify({"error": "Configurez d'abord {}.".format(source.label)}), 400
    if not source.is_authenticated():
        return jsonify({
            "error": "Connectez votre compte {} avant de lancer l'opération.".format(
                source.label
            )
        }), 401
    if not destination.is_configured():
        return jsonify({
            "error": "Configurez d'abord {}.".format(destination.label)
        }), 400
    if not destination.is_authenticated():
        return jsonify({
            "error": "Connectez votre compte {} avant de lancer l'opération.".format(
                destination.label
            )
        }), 401

    raw_options = data.get("options") or {}
    options = {
        "dedupe": bool(raw_options.get("dedupe", True)),
        "dedupe_isrc": bool(raw_options.get("dedupe_isrc", False)),
        "dry_run": bool(raw_options.get("dry_run", False)),
    }

    try:
        source_playlists = source.list_playlists()
    except Exception as exc:  # noqa: BLE001 - converti en réponse HTTP
        return provider_error_response(exc)

    source_map = {
        playlist.get("id"): playlist for playlist in source_playlists
    }
    for playlist_id in playlist_ids:
        playlist = source_map.get(playlist_id)
        if not playlist or not playlist.get("can_read", True):
            return jsonify({
                "error": "Certaines playlists sélectionnées sont introuvables ou illisibles."
            }), 400

    sources = [
        {
            "id": playlist_id,
            "name": source_map[playlist_id].get("name") or "(sans titre)",
        }
        for playlist_id in playlist_ids
    ]

    if provider == "spotify" and mode == "existing":
        try:
            me = destination.client.me()
            playlist = destination.client.get_playlist(target["id"])
        except Exception as exc:  # noqa: BLE001 - converti en réponse HTTP
            return provider_error_response(exc)
        owner = (playlist.get("owner") or {}).get("id")
        if owner != me.get("id") and not playlist.get("collaborative"):
            return jsonify({
                "error": "Spotify ne permet de modifier que vos playlists ou celles où vous collaborez. Choisissez une autre destination."
            }), 400

    job_id = uuid.uuid4().hex
    job = {
        "status": "running",
        "phase": "collect",
        "message": "Préparation…",
        "progress": {"done": 0, "total": len(sources)},
        "added": 0,
        "source_provider": source_id,
        "destination_provider": provider,
        "provider": provider,
        "result": None,
        "error": None,
        "cancel": False,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    worker = threading.Thread(
        target=run_merge,
        args=(job_id, source_id, provider, sources, target, options),
        daemon=True,
    )
    worker.start()
    return jsonify({"job_id": job_id})


def public_job(job):
    return {key: value for key, value in job.items() if key != "cancel"}


@app.route("/api/merge/<job_id>")
def api_merge_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Tâche introuvable."}), 404
    return jsonify(public_job(job))


@app.route("/api/merge/<job_id>/cancel", methods=["POST"])
def api_merge_cancel(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Tâche introuvable."}), 404
    job["cancel"] = True
    return jsonify({"ok": True})


def run_merge(job_id, source_id, destination_id, sources, target, options):
    job = JOBS[job_id]
    try:
        source = get_music_provider(source_id)
        destination = (
            source
            if destination_id == source_id
            else get_music_provider(destination_id)
        )
        result = perform_transfer(
            source, destination, job, sources, target, options
        )
        job["result"] = result
        job["status"] = "cancelled" if job["cancel"] else "done"
        job["message"] = "Terminé."
    except SpotifyAuthError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except SpotifyAPIError as exc:
        job["status"] = "error"
        job["error"] = "Erreur Spotify ({}): {}".format(exc.status, exc.message)
    except AppleMusicAuthError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except AppleMusicConfigError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except AppleMusicAPIError as exc:
        job["status"] = "error"
        job["error"] = "Erreur Apple Music ({}): {}".format(exc.status, exc.message)
    except DeezerAuthError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except DeezerConfigError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except DeezerAPIError as exc:
        job["status"] = "error"
        job["error"] = "Erreur Deezer ({}): {}".format(exc.status, exc.message)
    except YouTubeMusicAuthError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except YouTubeMusicConfigError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except YouTubeMusicAPIError as exc:
        job["status"] = "error"
        job["error"] = "Erreur YouTube Music ({}): {}".format(exc.status, exc.message)
    except MergeError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - remonté dans l'interface
        job["status"] = "error"
        job["error"] = "Erreur inattendue : {}".format(exc)


def perform_transfer(source, destination, job, sources, target, options):
    tracks, stats, dup_preview = collect_tracks(source, job, sources, options)
    return perform_destination_transfer(
        source, destination, job, tracks, target, options, stats, dup_preview
    )


def collect_tracks(source, job, sources, options):
    stats = {
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
    dup_preview = []
    seen_keys = set()
    seen_isrc = set()
    tracks = []

    job["phase"] = "collect"
    job["progress"] = {"done": 0, "total": len(sources)}
    for index, source_playlist in enumerate(sources):
        if job["cancel"]:
            break
        job["message"] = "Lecture de « {} » depuis {} ({}/{})".format(
            source_playlist["name"], source.label, index + 1, len(sources)
        )
        try:
            result = source.read_playlist(source_playlist["id"])
        except Exception as exc:  # noqa: BLE001 - erreurs fournisseur
            status = getattr(exc, "status", None)
            if status in (403, 404, 800):
                stats["skipped_playlists"].append(source_playlist["name"])
                job["progress"]["done"] = index + 1
                continue
            raise

        stats["episodes"] += result.get("episodes", 0)
        stats["local_files"] += result.get("local_files", 0)
        stats["unavailable"] += result.get("unavailable", 0)

        for track in result.get("tracks") or []:
            stats["tracks_read"] += 1
            isrc = (track.get("isrc") or "").strip() or None
            key = (
                track.get("source_key")
                or track.get("direct_id")
                or track.get("uri")
                or isrc
                or "{}|{}".format(
                    "|".join(track.get("artists") or []).lower(),
                    track.get("title") or "",
                )
            )
            duplicate = False
            if options["dedupe"]:
                if key in seen_keys:
                    duplicate = True
                elif options["dedupe_isrc"] and isrc and isrc in seen_isrc:
                    duplicate = True
            if duplicate:
                stats["duplicates"] += 1
                if len(dup_preview) < DUP_PREVIEW_LIMIT:
                    dup_preview.append(
                        track.get("label")
                        or "{} — {}".format(
                            ", ".join(track.get("artists") or []) or "?",
                            track.get("title") or "?",
                        )
                    )
                continue
            seen_keys.add(key)
            if isrc:
                seen_isrc.add(isrc)
            track["source_provider"] = source.provider_id
            tracks.append(track)

        job["progress"]["done"] = index + 1

    return tracks, stats, dup_preview


def perform_destination_transfer(
    source, destination, job, tracks, target, options, stats, dup_preview
):
    """Transfère des morceaux normalisés vers un fournisseur de destination."""
    stats.setdefault("matched_direct", 0)
    unmatched_preview = []
    existing = {"ids": set(), "isrcs": set()}
    target_id = None
    playlist_name = target.get("name") or ""

    if target["mode"] == "existing":
        target_id = target["id"]
        job["message"] = "Lecture de la playlist {} cible…".format(destination.label)
        try:
            existing = destination.existing_tracks(target_id)
        except Exception as exc:  # noqa: BLE001 - erreurs normalisées ci-dessous
            status = getattr(exc, "status", None)
            if status in (403, 404, 800):
                raise MergeError(
                    "Impossible de lire la playlist {} cible. Vérifiez qu'elle existe "
                    "dans votre bibliothèque.".format(destination.label)
                )
            raise
        playlist_name = target.get("name") or "Playlist {}".format(destination.label)

    cache = {}
    destination_ids = []
    seen_destination_ids = set()
    total = len(tracks)
    job["phase"] = "match"
    job["progress"] = {"done": 0, "total": total}

    for index, track in enumerate(tracks):
        if job["cancel"]:
            break
        if (
            track.get("source_provider") == destination.provider_id
            and track.get("source_key")
        ):
            cache_key = "direct:{}".format(track["source_key"])
        elif track.get("isrc"):
            cache_key = "isrc:{}".format(track["isrc"].upper())
        else:
            cache_key = "search:{}|{}".format(
                "|".join(track.get("artists") or []).lower(),
                track.get("title") or "",
            )

        if cache_key in cache:
            destination_id, method = cache[cache_key]
        else:
            destination_id, method = destination.resolve_track(track)
            cache[cache_key] = (destination_id, method)

        if destination_id:
            if method == "direct":
                stats["matched_direct"] += 1
            elif method == "isrc":
                stats["matched_isrc"] += 1
            else:
                stats["matched_search"] += 1

            isrc = (track.get("isrc") or "").upper()
            already = destination_id in existing["ids"] or (
                isrc and isrc in existing["isrcs"]
            )
            if options["dedupe"] and already:
                stats["already_in_target"] += 1
            elif options["dedupe"] and destination_id in seen_destination_ids:
                stats["duplicates"] += 1
            else:
                destination_ids.append(destination_id)
                seen_destination_ids.add(destination_id)
        else:
            stats["unmatched"] += 1
            if len(unmatched_preview) < DUP_PREVIEW_LIMIT:
                unmatched_preview.append(track.get("label") or track.get("title") or "?")

        job["progress"]["done"] = index + 1
        if index % 3 == 0 or index + 1 == total:
            if source.provider_id == destination.provider_id:
                job["message"] = "Préparation des morceaux… {}/{} · {} retenu(s)".format(
                    index + 1, total, len(destination_ids)
                )
            else:
                job["message"] = "Recherche sur {}… {}/{} · {} trouvé(s)".format(
                    destination.label, index + 1, total, len(destination_ids)
                )

    result = {
        "source_provider": source.provider_id,
        "source_label": source.label,
        "provider": destination.provider_id,
        "provider_label": destination.label,
        "created": False,
        "dry_run": options["dry_run"],
        "playlist_id": target_id,
        "playlist_url": destination.playlist_url(target_id) if target_id else None,
        "playlist_name": playlist_name,
        "to_add": len(destination_ids),
        "added": 0,
        "stats": stats,
        "dup_preview": dup_preview,
        "unmatched_preview": unmatched_preview,
    }

    if job["cancel"] or options["dry_run"]:
        return result

    limit = destination.max_playlist_items
    if target["mode"] == "new":
        if not destination_ids:
            raise MergeError(
                "Aucun morceau n'a pu être trouvé sur {} : la playlist serait vide.".format(
                    destination.label
                )
            )
        if limit and len(destination_ids) > limit:
            raise MergeError(
                "Impossible : l'opération contient {} morceaux, or {} limite les playlists "
                "à {} morceaux.".format(
                    len(destination_ids), destination.label, limit
                )
            )
        job["phase"] = "target"
        job["message"] = "Création de la playlist « {} » sur {}…".format(
            target["name"].strip(), destination.label
        )
        description = (target.get("description") or "").strip()
        if not description:
            description = "Playlist générée par Spotify Playlist Merger"
        created_playlist = destination.create_playlist(
            target["name"].strip(),
            description,
            bool(target.get("public")),
        )
        target_id = created_playlist.get("id")
        if not target_id:
            raise MergeError(
                "{} n'a pas renvoyé l'identifiant de la playlist créée.".format(
                    destination.label
                )
            )
        result["created"] = True
        result["playlist_id"] = target_id
        result["playlist_url"] = destination.playlist_url(target_id)
        result["playlist_name"] = created_playlist.get("name") or target["name"]
    else:
        if limit and len(existing["ids"]) + len(destination_ids) > limit:
            raise MergeError(
                "Impossible : la playlist cible contiendrait plus de {} morceaux.".format(
                    limit
                )
            )

    job["phase"] = "add"
    job["progress"] = {"done": 0, "total": len(destination_ids)}
    job["message"] = "Ajout des morceaux… 0/{}".format(len(destination_ids))
    for batch in chunks(destination_ids, destination.batch_size):
        if job["cancel"]:
            break
        destination.add_tracks(target_id, batch)
        job["added"] += len(batch)
        job["progress"]["done"] = job["added"]
        job["message"] = "Ajout des morceaux… {}/{}".format(
            job["added"], len(destination_ids)
        )

    result["added"] = job["added"]
    return result


if __name__ == "__main__":
    print("Spotify Playlist Merger")
    print("Ouvrez http://{}:{} dans votre navigateur (Ctrl+C pour quitter)".format(HOST, PORT))
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
