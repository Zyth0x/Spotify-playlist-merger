import os
import re
import secrets
import threading
import uuid

from flask import Flask, jsonify, redirect, render_template, request, session

from apple_client import (
    AppleMusicAPIError,
    AppleMusicAuthError,
    AppleMusicClient,
    AppleMusicConfigError,
    validate_private_key,
)
from matching import pick_best_candidate
from spotify_client import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyClient,
    build_authorize_url,
    generate_pkce_pair,
    load_json_file,
    save_json_file,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
TOKENS_PATH = os.path.join(DATA_DIR, "tokens.json")
APPLE_KEY_PATH = os.path.join(DATA_DIR, "apple_key.p8")
APPLE_TOKENS_PATH = os.path.join(DATA_DIR, "apple_tokens.json")

HOST = "127.0.0.1"
PORT = 8888
ORIGIN = "http://{}:{}".format(HOST, PORT)
APPLE_ORIGINS = [ORIGIN, "http://localhost:{}".format(PORT)]
REDIRECT_URI = ORIGIN + "/callback"

BATCH_SIZE = 100
APPLE_BATCH_SIZE = 100
MAX_PLAYLIST_ITEMS = 10000
DUP_PREVIEW_LIMIT = 50

TARGET_PROVIDERS = ("spotify", "apple_music")


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


def track_label(item):
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or [])
    name = item.get("name") or item.get("uri") or "?"
    return "{} — {}".format(artists or "?", name)


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


def apple_artwork(attributes):
    artwork = attributes.get("artwork") or {}
    url = artwork.get("url")
    if not url:
        return None
    return url.replace("{w}x{h}", "60x60")


def apple_playlist_url(playlist_id):
    return "https://music.apple.com/library/playlist/{}".format(playlist_id)


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
    return jsonify({
        "has_client_id": bool(client_id),
        "authenticated": authenticated,
        "me": me,
        "apple": apple,
        "providers": {
            "spotify": {
                "configured": bool(client_id),
                "authenticated": authenticated,
                "me": me,
            },
            "apple_music": apple,
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
    try:
        client = get_client()
        me = client.me()
        raw_playlists = client.all_playlists()
    except SpotifyAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except SpotifyAPIError as exc:
        return jsonify({"error": exc.message}), 502

    my_id = me.get("id")
    playlists = []
    for pl in raw_playlists:
        owner = pl.get("owner") or {}
        total = None
        items = pl.get("items")
        if isinstance(items, dict):
            total = items.get("total")
        if total is None:
            legacy = pl.get("tracks")
            if isinstance(legacy, dict):
                total = legacy.get("total")
        owned = owner.get("id") == my_id
        collaborative = bool(pl.get("collaborative"))
        images = pl.get("images") or []
        playlists.append({
            "id": pl.get("id"),
            "name": pl.get("name") or "(sans titre)",
            "track_count": total,
            "owner": owner.get("display_name") or owner.get("id") or "?",
            "owned": owned,
            "collaborative": collaborative,
            "can_read": owned or collaborative,
            "image": images[0].get("url") if images else None,
        })
    return jsonify({"playlists": playlists})


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


# --------------------------------------------------------------------- merge


@app.route("/api/merge", methods=["POST"])
def api_merge():
    data = request.get_json(silent=True) or {}
    playlist_ids = list(dict.fromkeys(data.get("playlist_ids") or []))
    if not playlist_ids:
        return jsonify({"error": "Sélectionnez au moins une playlist source."}), 400

    target = data.get("target") or {}
    provider = target.get("provider") or "spotify"
    if provider not in TARGET_PROVIDERS:
        return jsonify({"error": "Fournisseur de destination inconnu."}), 400

    mode = target.get("mode")
    if mode not in ("new", "existing"):
        return jsonify({"error": "Destination invalide."}), 400
    if mode == "new" and not (target.get("name") or "").strip():
        return jsonify({"error": "Indiquez un nom pour la nouvelle playlist."}), 400
    if mode == "existing" and not target.get("id"):
        return jsonify({"error": "Choisissez une playlist existante."}), 400

    apple = None
    if provider == "apple_music":
        apple = get_apple_client()
        if not apple.is_configured():
            return jsonify({"error": "Configurez d'abord Apple Music (Team ID, Key ID et clé .p8)."}), 400
        if not apple.is_authenticated():
            return jsonify({"error": "Connectez votre compte Apple Music avant de lancer le transfert."}), 401

    raw_options = data.get("options") or {}
    options = {
        "dedupe": bool(raw_options.get("dedupe", True)),
        "dedupe_isrc": bool(raw_options.get("dedupe_isrc", False)),
        "dry_run": bool(raw_options.get("dry_run", False)),
    }

    try:
        client = get_client()
        me = client.me()
    except SpotifyAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except SpotifyAPIError as exc:
        return jsonify({"error": exc.message}), 502

    try:
        names = {pl.get("id"): pl.get("name") or "(sans titre)" for pl in client.all_playlists()}
    except SpotifyAuthError as exc:
        return jsonify({"error": str(exc)}), 401
    except SpotifyAPIError as exc:
        return jsonify({"error": exc.message}), 502

    sources = [{"id": pid, "name": names.get(pid, pid)} for pid in playlist_ids]
    if len(sources) != len(playlist_ids):
        return jsonify({"error": "Certaines playlists sélectionnées sont introuvables."}), 400

    if provider == "spotify" and mode == "existing":
        try:
            playlist = client.get_playlist(target["id"])
        except SpotifyAuthError as exc:
            return jsonify({"error": str(exc)}), 401
        except SpotifyAPIError as exc:
            return jsonify({"error": exc.message}), 502
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
        "provider": provider,
        "result": None,
        "error": None,
        "cancel": False,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    client_id = load_config()["client_id"]
    worker = threading.Thread(
        target=run_merge,
        args=(job_id, client_id, provider, sources, target, options),
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


def run_merge(job_id, client_id, provider, sources, target, options):
    job = JOBS[job_id]
    client = SpotifyClient(client_id, TOKENS_PATH)
    apple = get_apple_client() if provider == "apple_music" else None
    try:
        result = perform_merge(client, apple, job, provider, sources, target, options)
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
    except MergeError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - remonté dans l'interface
        job["status"] = "error"
        job["error"] = "Erreur inattendue : {}".format(exc)


def perform_merge(client, apple, job, provider, sources, target, options):
    tracks, stats, dup_preview = collect_tracks(client, job, sources, options)
    if provider == "apple_music":
        return perform_apple_transfer(apple, job, tracks, target, options, stats, dup_preview)
    return perform_spotify_merge(client, job, tracks, target, options, stats, dup_preview)


def collect_tracks(client, job, sources, options):
    stats = {
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
    dup_preview = []
    seen_uris = set()
    seen_isrc = set()
    tracks = []

    job["phase"] = "collect"
    job["progress"] = {"done": 0, "total": len(sources)}
    for index, source in enumerate(sources):
        if job["cancel"]:
            break
        job["message"] = "Lecture de « {} » ({}/{})".format(
            source["name"], index + 1, len(sources)
        )
        try:
            for entry in client.playlist_items(source["id"]):
                item = entry.get("item") or entry.get("track")
                if not item:
                    stats["unavailable"] += 1
                    continue
                if item.get("type") != "track":
                    stats["episodes"] += 1
                    continue
                if item.get("is_local") or entry.get("is_local"):
                    stats["local_files"] += 1
                    continue
                uri = item.get("uri")
                if not uri:
                    stats["unavailable"] += 1
                    continue
                stats["tracks_read"] += 1
                isrc = (item.get("external_ids") or {}).get("isrc")
                duplicate = False
                if options["dedupe"]:
                    if uri in seen_uris:
                        duplicate = True
                    elif options["dedupe_isrc"] and isrc and isrc in seen_isrc:
                        duplicate = True
                if duplicate:
                    stats["duplicates"] += 1
                    if len(dup_preview) < DUP_PREVIEW_LIMIT:
                        dup_preview.append(track_label(item))
                    continue
                seen_uris.add(uri)
                if isrc:
                    seen_isrc.add(isrc)
                tracks.append({
                    "uri": uri,
                    "isrc": isrc,
                    "title": item.get("name") or "",
                    "artists": [
                        artist.get("name")
                        for artist in item.get("artists") or []
                        if artist.get("name")
                    ],
                    "album": (item.get("album") or {}).get("name") or "",
                    "duration_ms": item.get("duration_ms"),
                    "label": track_label(item),
                })
        except SpotifyAPIError as exc:
            if exc.status in (403, 404):
                stats["skipped_playlists"].append(source["name"])
                continue
            raise
        job["progress"]["done"] = index + 1

    return tracks, stats, dup_preview


def perform_spotify_merge(client, job, tracks, target, options, stats, dup_preview):
    uris = [track["uri"] for track in tracks]
    job["phase"] = "target"
    created = False
    target_id = None
    playlist_name = target.get("name") or ""
    target_url = None

    if job["cancel"]:
        return {
            "provider": "spotify",
            "created": False,
            "dry_run": options["dry_run"],
            "playlist_id": None,
            "playlist_url": None,
            "playlist_name": playlist_name,
            "to_add": len(uris),
            "added": 0,
            "stats": stats,
            "dup_preview": dup_preview,
        }

    if target["mode"] == "new":
        if not uris:
            raise MergeError("Aucun morceau à ajouter : les playlists sélectionnées sont vides ou illisibles.")
        if len(uris) > MAX_PLAYLIST_ITEMS:
            raise MergeError(
                "Impossible : la fusion contient {} morceaux, or Spotify limite les playlists à {} morceaux.".format(
                    len(uris), MAX_PLAYLIST_ITEMS
                )
            )
        if not options["dry_run"]:
            job["message"] = "Création de la playlist « {} »…".format(target["name"].strip())
            created_playlist = client.create_playlist(
                target["name"].strip(),
                bool(target.get("public")),
                (target.get("description") or "").strip() or "Fusion générée par Spotify Playlist Merger",
            )
            target_id = created_playlist.get("id")
            created = True
            playlist_name = created_playlist.get("name") or target["name"]
    else:
        target_id = target["id"]
        job["message"] = "Vérification du contenu existant de la playlist cible…"
        try:
            existing_uris = client.playlist_item_uris(target_id)
        except SpotifyAPIError as exc:
            if exc.status in (403, 404):
                raise MergeError(
                    "Impossible de lire le contenu de la playlist cible. Spotify ne permet de lire que vos playlists ou celles où vous collaborez."
                )
            raise
        if options["dedupe"]:
            stats["already_in_target"] = sum(1 for uri in uris if uri in existing_uris)
            uris = [uri for uri in uris if uri not in existing_uris]
        if len(existing_uris) + len(uris) > MAX_PLAYLIST_ITEMS:
            raise MergeError(
                "Impossible : la playlist cible contiendrait plus de {} morceaux.".format(MAX_PLAYLIST_ITEMS)
            )
        playlist_name = target.get("name") or "Playlist cible"

    if target_id:
        target_url = "https://open.spotify.com/playlist/{}".format(target_id)

    result = {
        "provider": "spotify",
        "created": created,
        "dry_run": options["dry_run"],
        "playlist_id": target_id,
        "playlist_url": target_url,
        "playlist_name": playlist_name,
        "to_add": len(uris),
        "added": 0,
        "stats": stats,
        "dup_preview": dup_preview,
    }

    if options["dry_run"]:
        return result

    job["phase"] = "add"
    job["progress"] = {"done": 0, "total": len(uris)}
    job["message"] = "Ajout des morceaux… 0/{}".format(len(uris))
    for batch in chunks(uris, BATCH_SIZE):
        if job["cancel"]:
            break
        client.add_items(target_id, batch)
        job["added"] += len(batch)
        job["progress"]["done"] = job["added"]
        job["message"] = "Ajout des morceaux… {}/{}".format(job["added"], len(uris))
    result["added"] = job["added"]
    return result


def resolve_apple_song(apple, track):
    """Retourne (morceau Apple Music, méthode) ou (None, None)."""
    isrc = (track.get("isrc") or "").strip()
    if isrc:
        try:
            song = apple.search_song_by_isrc(isrc)
            if song:
                return song, "isrc"
        except (AppleMusicAuthError, AppleMusicConfigError):
            raise
        except AppleMusicAPIError as exc:
            if exc.status in (401, 403, 429) or exc.status >= 500:
                raise

    term = "{} {}".format(
        track.get("title") or "",
        (track.get("artists") or [""])[0],
    ).strip()
    if not term:
        return None, None
    candidates = apple.search_songs(term, limit=10)
    song, _score = pick_best_candidate(track, candidates)
    if song:
        return song, "search"
    return None, None


def perform_apple_transfer(apple, job, tracks, target, options, stats, dup_preview):
    unmatched_preview = []
    existing = {"catalog_ids": set(), "isrcs": set()}
    target_id = None
    playlist_name = target.get("name") or ""

    if target["mode"] == "existing":
        target_id = target["id"]
        job["message"] = "Lecture de la playlist Apple Music cible…"
        try:
            existing = apple.playlist_existing_tracks(target_id)
        except AppleMusicAuthError:
            raise
        except AppleMusicAPIError as exc:
            if exc.status in (403, 404):
                raise MergeError(
                    "Impossible de lire la playlist Apple Music cible. Vérifiez qu'elle existe dans votre bibliothèque."
                )
            raise
        playlist_name = target.get("name") or "Playlist Apple Music"

    cache = {}
    song_ids = []
    seen_song_ids = set()
    total = len(tracks)
    job["phase"] = "match"
    job["progress"] = {"done": 0, "total": total}

    for index, track in enumerate(tracks):
        if job["cancel"]:
            break
        cache_key = (
            track["isrc"].upper()
            if track.get("isrc")
            else "{}|{}".format(
                "|".join(track.get("artists") or []).lower(),
                track.get("title") or "",
            )
        )
        if cache_key in cache:
            song, method = cache[cache_key]
        else:
            song, method = resolve_apple_song(apple, track)
            cache[cache_key] = (song, method)

        if song:
            stats["matched_isrc" if method == "isrc" else "matched_search"] += 1
            song_id = str(song.get("id"))
            isrc = (track.get("isrc") or "").upper()
            already = song_id in existing["catalog_ids"] or (isrc and isrc in existing["isrcs"])
            if options["dedupe"] and already:
                stats["already_in_target"] += 1
            elif options["dedupe"] and song_id in seen_song_ids:
                stats["duplicates"] += 1
            else:
                song_ids.append(song_id)
                seen_song_ids.add(song_id)
        else:
            stats["unmatched"] += 1
            if len(unmatched_preview) < DUP_PREVIEW_LIMIT:
                unmatched_preview.append(track["label"])

        job["progress"]["done"] = index + 1
        if index % 3 == 0 or index + 1 == total:
            job["message"] = "Recherche sur Apple Music… {}/{} · {} trouvé(s)".format(
                index + 1, total, len(song_ids)
            )

    result = {
        "provider": "apple_music",
        "created": False,
        "dry_run": options["dry_run"],
        "playlist_id": target_id,
        "playlist_url": apple_playlist_url(target_id) if target_id else None,
        "playlist_name": playlist_name,
        "to_add": len(song_ids),
        "added": 0,
        "stats": stats,
        "dup_preview": dup_preview,
        "unmatched_preview": unmatched_preview,
    }

    if job["cancel"] or options["dry_run"]:
        return result

    if target["mode"] == "new":
        if not song_ids:
            raise MergeError(
                "Aucun morceau n'a pu être trouvé sur Apple Music : la playlist serait vide."
            )
        job["phase"] = "target"
        job["message"] = "Création de la playlist « {} » sur Apple Music…".format(
            target["name"].strip()
        )
        created_playlist = apple.create_playlist(
            target["name"].strip(),
            (target.get("description") or "").strip(),
            bool(target.get("public")),
        )
        target_id = created_playlist.get("id")
        if not target_id:
            raise MergeError("Apple Music n'a pas renvoyé l'identifiant de la playlist créée.")
        result["created"] = True
        result["playlist_id"] = target_id
        result["playlist_url"] = apple_playlist_url(target_id)
        result["playlist_name"] = (
            (created_playlist.get("attributes") or {}).get("name")
            or target["name"]
        )
    remaining = song_ids

    job["phase"] = "add"
    job["progress"] = {"done": job["added"], "total": len(song_ids)}
    job["message"] = "Ajout des morceaux… {}/{}".format(job["added"], len(song_ids))
    for batch in chunks(remaining, APPLE_BATCH_SIZE):
        if job["cancel"]:
            break
        apple.add_tracks(target_id, batch)
        job["added"] += len(batch)
        job["progress"]["done"] = job["added"]
        job["message"] = "Ajout des morceaux… {}/{}".format(job["added"], len(song_ids))

    result["added"] = job["added"]
    return result


if __name__ == "__main__":
    print("Spotify Playlist Merger")
    print("Ouvrez http://{}:{} dans votre navigateur (Ctrl+C pour quitter)".format(HOST, PORT))
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
