import os
import secrets
import threading
import uuid

from flask import Flask, jsonify, redirect, render_template, request, session

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

HOST = "127.0.0.1"
PORT = 8888
REDIRECT_URI = "http://{}:{}/callback".format(HOST, PORT)

BATCH_SIZE = 100
MAX_PLAYLIST_ITEMS = 10000
DUP_PREVIEW_LIMIT = 50


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


def get_client():
    client_id = load_config().get("client_id")
    if not client_id:
        raise SpotifyAuthError("Client ID manquant. Configurez d'abord votre application Spotify.")
    return SpotifyClient(client_id, TOKENS_PATH)


def track_label(item):
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or [])
    name = item.get("name") or item.get("uri") or "?"
    return "{} — {}".format(artists or "?", name)


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


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
    return jsonify({"has_client_id": bool(client_id), "authenticated": authenticated, "me": me})


@app.route("/api/client-id", methods=["POST"])
def api_client_id():
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    if not client_id or len(client_id) > 64 or " " in client_id:
        return jsonify({"error": "Client ID invalide."}), 400
    save_json_file(CONFIG_PATH, {"client_id": client_id})
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


@app.route("/api/merge", methods=["POST"])
def api_merge():
    data = request.get_json(silent=True) or {}
    playlist_ids = list(dict.fromkeys(data.get("playlist_ids") or []))
    if not playlist_ids:
        return jsonify({"error": "Sélectionnez au moins une playlist source."}), 400

    target = data.get("target") or {}
    mode = target.get("mode")
    if mode not in ("new", "existing"):
        return jsonify({"error": "Destination invalide."}), 400
    if mode == "new" and not (target.get("name") or "").strip():
        return jsonify({"error": "Indiquez un nom pour la nouvelle playlist."}), 400
    if mode == "existing" and not target.get("id"):
        return jsonify({"error": "Choisissez une playlist existante."}), 400

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

    if mode == "existing":
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
        "result": None,
        "error": None,
        "cancel": False,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    client_id = load_config()["client_id"]
    worker = threading.Thread(
        target=run_merge,
        args=(job_id, client_id, sources, target, options),
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


def run_merge(job_id, client_id, sources, target, options):
    job = JOBS[job_id]
    client = SpotifyClient(client_id, TOKENS_PATH)
    try:
        result = perform_merge(client, job, sources, target, options)
        job["result"] = result
        job["status"] = "cancelled" if job["cancel"] else "done"
        job["message"] = "Terminé."
    except SpotifyAuthError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except SpotifyAPIError as exc:
        job["status"] = "error"
        job["error"] = "Erreur Spotify ({}): {}".format(exc.status, exc.message)
    except MergeError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
    except Exception as exc:
        job["status"] = "error"
        job["error"] = "Erreur inattendue : {}".format(exc)


def perform_merge(client, job, sources, target, options):
    stats = {
        "tracks_read": 0,
        "duplicates": 0,
        "episodes": 0,
        "local_files": 0,
        "unavailable": 0,
        "already_in_target": 0,
        "skipped_playlists": [],
    }
    dup_preview = []
    seen_uris = set()
    seen_isrc = set()
    uris = []

    job["phase"] = "collect"
    job["progress"] = {"done": 0, "total": len(sources)}
    for index, source in enumerate(sources):
        if job["cancel"]:
            break
        job["message"] = "Lecture de « {} » ({}/{})".format(source["name"], index + 1, len(sources))
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
                uris.append(uri)
        except SpotifyAPIError as exc:
            if exc.status in (403, 404):
                stats["skipped_playlists"].append(source["name"])
                continue
            raise
        job["progress"]["done"] = index + 1

    job["phase"] = "target"
    created = False
    target_id = None
    playlist_name = target.get("name") or ""
    target_url = None

    if job["cancel"]:
        return {
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


if __name__ == "__main__":
    print("Spotify Playlist Merger")
    print("Ouvrez http://{}:{} dans votre navigateur (Ctrl+C pour quitter)".format(HOST, PORT))
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
