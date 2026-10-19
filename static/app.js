const state = {
  playlists: [],
  selected: new Set(),
  jobId: null,
  jobTimer: null,
  me: null,
  apple: {
    configured: false,
    authenticated: false,
    storefront: "fr",
    team_id: null,
    key_id: null,
    playlists: [],
    playlistsLoaded: false,
    playlistsLoading: false,
  },
  showAppleSetup: false,
  appleMusic: {
    scriptPromise: null,
    ready: null,
    instance: null,
  },
};

const el = (id) => document.getElementById(id);

const views = {
  setup: el("view-setup"),
  login: el("view-login"),
  merger: el("view-merger"),
};

function showView(name) {
  Object.entries(views).forEach(([key, node]) => node.classList.toggle("hidden", key !== name));
}

function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function api(path, options = {}) {
  const opts = Object.assign({}, options);
  if (opts.body) {
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  }
  const resp = await fetch(path, opts);
  let data = null;
  try {
    data = await resp.json();
  } catch (err) {
    data = null;
  }
  if (!resp.ok) {
    throw new Error((data && data.error) || `Erreur HTTP ${resp.status}`);
  }
  return data;
}

function banner(message, kind = "") {
  const node = el("banner");
  node.textContent = message;
  node.className = "banner " + kind;
  setTimeout(() => node.classList.add("hidden"), 6000);
}

async function boot() {
  const params = new URLSearchParams(window.location.search);
  if (params.get("connected")) banner("Connexion à Spotify réussie.", "ok");
  if (params.get("auth_error")) banner("La connexion à Spotify a échoué. Réessayez.", "error");
  if (params.toString()) window.history.replaceState(null, "", "/");

  try {
    const data = await api("/api/state");
    if (!data.has_client_id) {
      showView("setup");
      return;
    }
    if (!data.authenticated) {
      showView("login");
      return;
    }
    state.me = data.me;
    if (data.apple) Object.assign(state.apple, data.apple);
    renderUser(data.me);
    showView("merger");
    renderTargetOptions();
    renderAppleSection();
    syncTargetFields("spotify");
    syncTargetFields("apple");
    loadPlaylists();
  } catch (err) {
    banner(err.message, "error");
    showView("setup");
  }
}

function renderUser(me) {
  if (!me) return;
  el("user-box").classList.remove("hidden");
  el("user-name").textContent = me.display_name || "";
  if (me.image) {
    const img = el("user-avatar");
    img.src = me.image;
    img.classList.remove("hidden");
  } else {
    const fallback = el("user-avatar-fallback");
    fallback.textContent = (me.display_name || "?").slice(0, 1).toUpperCase();
    fallback.classList.remove("hidden");
  }
}

async function saveClientId(inputId) {
  const input = el(inputId);
  const clientId = input.value.trim();
  if (!clientId) {
    banner("Collez votre Client ID Spotify.", "error");
    return;
  }
  try {
    await api("/api/client-id", { method: "POST", body: JSON.stringify({ client_id: clientId }) });
    window.location.href = "/";
  } catch (err) {
    banner(err.message, "error");
  }
}

async function loadPlaylists() {
  const list = el("playlist-list");
  list.innerHTML = '<li class="loading">Chargement de vos playlists…</li>';
  try {
    const data = await api("/api/playlists");
    state.playlists = data.playlists || [];
    renderPlaylists();
    renderTargetOptions();
  } catch (err) {
    list.innerHTML = `<li class="loading">${escapeHtml(err.message)}</li>`;
    if (err.message.toLowerCase().includes("reconnect")) showView("login");
  }
}

function countLabel(playlist) {
  const n = playlist.track_count;
  if (n == null) return "nombre de morceaux inconnu";
  return n + (n === 1 ? " morceau" : " morceaux");
}

function renderPlaylists() {
  const list = el("playlist-list");
  const query = el("filter").value.trim().toLowerCase();
  const visible = state.playlists.filter(
    (p) => !query || (p.name || "").toLowerCase().includes(query)
  );
  list.innerHTML = "";
  if (!visible.length) {
    list.innerHTML = '<li class="loading">Aucune playlist trouvée.</li>';
    updateSelectionCount();
    return;
  }
  for (const playlist of visible) {
    const li = document.createElement("li");
    li.className = "playlist-item" + (playlist.can_read ? "" : " disabled");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selected.has(playlist.id);
    checkbox.disabled = !playlist.can_read;
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(playlist.id);
      else state.selected.delete(playlist.id);
      updateSelectionCount();
    });

    const label = document.createElement("label");
    if (playlist.image) {
      const img = document.createElement("img");
      img.src = playlist.image;
      img.alt = "";
      label.appendChild(img);
    } else {
      const ph = document.createElement("div");
      ph.className = "ph";
      label.appendChild(ph);
    }
    const meta = document.createElement("div");
    meta.className = "meta";
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = playlist.name;
    const sub = document.createElement("div");
    sub.className = "sub";
    const ownerLabel = playlist.owned ? "vous" : playlist.owner;
    sub.textContent = `${countLabel(playlist)} · ${ownerLabel}`;
    meta.appendChild(name);
    meta.appendChild(sub);
    label.prepend(checkbox);
    label.appendChild(meta);

    if (!playlist.can_read) {
      li.title = "Spotify (mode développement) ne permet de lire que vos playlists ou celles où vous collaborez.";
    }
    li.appendChild(label);
    list.appendChild(li);
  }
  updateSelectionCount();
}

function updateSelectionCount() {
  const n = state.selected.size;
  el("selection-count").textContent = n + (n === 1 ? " playlist sélectionnée" : " playlists sélectionnées");
}

function renderTargetOptions() {
  const select = el("target-select");
  const options = state.playlists.filter((p) => p.can_read);
  select.innerHTML = "";
  if (!options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Aucune playlist modifiable";
    select.appendChild(option);
    return;
  }
  for (const playlist of options) {
    const option = document.createElement("option");
    option.value = playlist.id;
    option.textContent = playlist.name;
    select.appendChild(option);
  }
}

function currentProvider() {
  return document.querySelector('input[name="target-provider"]:checked').value;
}

function currentTargetMode(prefix) {
  return document.querySelector(`input[name="${prefix}-target-mode"]:checked`).value;
}

function syncProviderFields() {
  const provider = currentProvider();
  el("provider-spotify").classList.toggle("hidden", provider !== "spotify");
  el("provider-apple").classList.toggle("hidden", provider !== "apple_music");
  if (provider === "apple_music") {
    renderAppleSection();
    if (state.apple.configured) {
      prepareAppleMusic().catch(() => {
        /* l'erreur sera affichée au moment de la connexion */
      });
    }
  }
}

function syncTargetFields(prefix) {
  const mode = currentTargetMode(prefix);
  if (prefix === "spotify") {
    el("new-fields").classList.toggle("hidden", mode !== "new");
    el("existing-fields").classList.toggle("hidden", mode !== "existing");
  } else {
    el("apple-new-fields").classList.toggle("hidden", mode !== "new");
    el("apple-existing-fields").classList.toggle("hidden", mode !== "existing");
  }
}

// ------------------------------------------------------------- Apple Music

function loadMusicKitScript() {
  if (window.MusicKit) return Promise.resolve();
  if (state.appleMusic.scriptPromise) return state.appleMusic.scriptPromise;
  state.appleMusic.scriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://js-cdn.music.apple.com/musickit/v3/musickit.js";
    script.async = true;
    script.onload = () => {
      if (window.MusicKit) resolve();
      else document.addEventListener("musickitloaded", () => resolve(), { once: true });
    };
    script.onerror = () => reject(new Error("Impossible de charger MusicKit. Vérifiez votre connexion."));
    document.head.appendChild(script);
  });
  return state.appleMusic.scriptPromise;
}

async function prepareAppleMusic() {
  if (state.appleMusic.instance) return state.appleMusic.instance;
  if (state.appleMusic.ready) return state.appleMusic.ready;
  state.appleMusic.ready = (async () => {
    const data = await api("/api/apple/developer-token");
    await loadMusicKitScript();
    const configured = await MusicKit.configure({
      developerToken: data.developer_token,
      storefrontId: data.storefront || "fr",
      app: { name: "Spotify Playlist Merger", build: "1.0" },
    });
    const instance = configured && typeof configured.authorize === "function"
      ? configured
      : MusicKit.getInstance();
    state.appleMusic.instance = instance;
    return instance;
  })().catch((err) => {
    state.appleMusic.ready = null;
    throw err;
  });
  return state.appleMusic.ready;
}

function renderAppleSection() {
  const apple = state.apple;
  const setupVisible = !apple.configured || state.showAppleSetup;
  el("apple-setup").classList.toggle("hidden", !setupVisible);
  el("apple-setup-details").open = setupVisible;
  el("apple-connect").classList.toggle("hidden", !apple.configured || apple.authenticated);
  el("apple-connected").classList.toggle("hidden", !apple.authenticated);
  el("apple-target").classList.toggle("hidden", !apple.authenticated);

  if (apple.team_id) el("apple-team-id").value = apple.team_id;
  if (apple.key_id) el("apple-key-id").value = apple.key_id;
  if (apple.storefront) {
    el("apple-storefront").value = apple.storefront;
    el("apple-storefront-label").textContent = "(storefront " + apple.storefront.toUpperCase() + ")";
  }

  if (
    apple.authenticated &&
    currentProvider() === "apple_music" &&
    !apple.playlistsLoaded &&
    !apple.playlistsLoading
  ) {
    loadApplePlaylists();
  }
}

async function saveAppleConfig(event) {
  event.preventDefault();
  const privateKey = el("apple-key-input").value.trim();
  const payload = {
    team_id: el("apple-team-id").value.trim(),
    key_id: el("apple-key-id").value.trim(),
    storefront: el("apple-storefront").value.trim().toLowerCase() || "fr",
    private_key: privateKey,
  };
  if (!payload.team_id || !payload.key_id) {
    banner("Renseignez le Team ID et le Key ID.", "error");
    return;
  }
  if (!state.apple.configured && !privateKey) {
    banner("Sélectionnez ou collez votre clé privée .p8.", "error");
    return;
  }
  try {
    const data = await api("/api/apple/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    Object.assign(state.apple, data.apple, { playlists: [], playlistsLoaded: false });
    state.showAppleSetup = false;
    el("apple-key-input").value = "";
    el("apple-key-file").value = "";
    renderAppleSection();
    banner("Configuration Apple Music enregistrée.", "ok");

    if (state.appleMusic.instance) {
      window.location.reload();
      return;
    }
    if (state.apple.configured) {
      prepareAppleMusic().catch(() => {
        /* l'erreur sera affichée au moment de la connexion */
      });
    }
  } catch (err) {
    banner(err.message, "error");
  }
}

function openAppleSetup() {
  state.showAppleSetup = true;
  renderAppleSection();
}

async function connectAppleMusic() {
  const button = el("apple-connect-btn");
  button.disabled = true;
  let music = null;
  try {
    music = await prepareAppleMusic();
    const token = await music.authorize();
    if (!token) throw new Error("Apple Music n'a pas renvoyé de jeton utilisateur.");
    const data = await api("/api/apple/session", {
      method: "POST",
      body: JSON.stringify({ music_user_token: token }),
    });
    Object.assign(state.apple, data.apple, { playlists: [], playlistsLoaded: false });
    state.showAppleSetup = false;
    renderAppleSection();
    banner("Connexion à Apple Music réussie.", "ok");
  } catch (err) {
    const message = err && err.message ? err.message : "Connexion à Apple Music impossible.";
    if (music && /session|reconnect|invalide/i.test(message)) {
      try {
        await music.unauthorize();
      } catch (unauthorizeError) {
        /* la prochaine tentative redemandera l'autorisation */
      }
    }
    banner(message, "error");
  } finally {
    button.disabled = false;
  }
}

async function disconnectAppleMusic() {
  try {
    if (state.appleMusic.instance && typeof state.appleMusic.instance.unauthorize === "function") {
      await state.appleMusic.instance.unauthorize();
    }
  } catch (err) {
    /* le jeton local est supprimé quoi qu'il arrive */
  }
  try {
    await api("/api/apple/logout", { method: "POST" });
    state.apple.authenticated = false;
    state.apple.playlists = [];
    state.apple.playlistsLoaded = false;
    renderAppleSection();
    banner("Compte Apple Music déconnecté.", "");
  } catch (err) {
    banner(err.message, "error");
  }
}

async function loadApplePlaylists() {
  if (!state.apple.authenticated || state.apple.playlistsLoading) return;
  state.apple.playlistsLoading = true;
  const select = el("apple-target-select");
  select.innerHTML = '<option value="">Chargement…</option>';
  try {
    const data = await api("/api/apple/playlists");
    state.apple.playlists = data.playlists || [];
    state.apple.playlistsLoaded = true;
    renderAppleTargetOptions();
  } catch (err) {
    select.innerHTML = '<option value="">Erreur de chargement</option>';
    banner(err.message, "error");
    if (/session|reconnect|non connecté/i.test(err.message)) {
      state.apple.authenticated = false;
      state.apple.playlistsLoaded = false;
      renderAppleSection();
    }
  } finally {
    state.apple.playlistsLoading = false;
  }
}

function renderAppleTargetOptions() {
  const select = el("apple-target-select");
  const options = (state.apple.playlists || []).filter((p) => p.can_edit !== false);
  select.innerHTML = "";
  if (!options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Aucune playlist Apple Music modifiable";
    select.appendChild(option);
    return;
  }
  for (const playlist of options) {
    const option = document.createElement("option");
    option.value = playlist.id;
    option.textContent = playlist.name;
    select.appendChild(option);
  }
}

// ------------------------------------------------------------------- merge

async function startMerge() {
  const errorNode = el("start-error");
  errorNode.classList.add("hidden");
  const ids = Array.from(state.selected);
  if (!ids.length) {
    errorNode.textContent = "Sélectionnez au moins une playlist source.";
    errorNode.classList.remove("hidden");
    return;
  }

  const provider = currentProvider();
  let target;
  if (provider === "apple_music") {
    if (!state.apple.configured) {
      errorNode.textContent = "Configurez d'abord Apple Music.";
      errorNode.classList.remove("hidden");
      return;
    }
    if (!state.apple.authenticated) {
      errorNode.textContent = "Connectez votre compte Apple Music avant de lancer le transfert.";
      errorNode.classList.remove("hidden");
      return;
    }
    const mode = currentTargetMode("apple");
    if (mode === "new") {
      target = {
        provider,
        mode,
        name: el("apple-new-name").value.trim(),
        description: el("apple-new-desc").value.trim(),
        public: el("apple-new-public").checked,
      };
      if (!target.name) {
        errorNode.textContent = "Indiquez un nom pour la nouvelle playlist Apple Music.";
        errorNode.classList.remove("hidden");
        return;
      }
    } else {
      const id = el("apple-target-select").value;
      if (!id) {
        errorNode.textContent = "Choisissez une playlist Apple Music existante.";
        errorNode.classList.remove("hidden");
        return;
      }
      const playlist = (state.apple.playlists || []).find((p) => p.id === id);
      target = { provider, mode, id, name: playlist ? playlist.name : "" };
    }
  } else {
    const mode = currentTargetMode("spotify");
    if (mode === "new") {
      target = {
        provider,
        mode,
        name: el("new-name").value.trim(),
        description: el("new-desc").value.trim(),
        public: el("new-public").checked,
      };
      if (!target.name) {
        errorNode.textContent = "Indiquez un nom pour la nouvelle playlist.";
        errorNode.classList.remove("hidden");
        return;
      }
    } else {
      target = { provider, mode, id: el("target-select").value };
      if (!target.id) {
        errorNode.textContent = "Choisissez une playlist existante.";
        errorNode.classList.remove("hidden");
        return;
      }
    }
  }

  const options = {
    dedupe: el("opt-dedupe").checked,
    dedupe_isrc: el("opt-isrc").checked,
    dry_run: el("opt-dry").checked,
  };

  el("start-btn").disabled = true;
  el("result-card").classList.add("hidden");
  el("progress-title").textContent = provider === "apple_music" ? "Transfert en cours…" : "Fusion en cours…";
  try {
    const data = await api("/api/merge", {
      method: "POST",
      body: JSON.stringify({ playlist_ids: ids, target, options }),
    });
    state.jobId = data.job_id;
    el("progress-card").classList.remove("hidden");
    el("progress-fill").style.width = "3%";
    pollJob();
  } catch (err) {
    errorNode.textContent = err.message;
    errorNode.classList.remove("hidden");
    el("start-btn").disabled = false;
  }
}

async function pollJob() {
  clearTimeout(state.jobTimer);
  try {
    const job = await api(`/api/merge/${state.jobId}`);
    updateProgress(job);
    if (job.status === "running") {
      state.jobTimer = setTimeout(pollJob, 800);
    } else {
      finishJob(job);
    }
  } catch (err) {
    clearTimeout(state.jobTimer);
    el("start-btn").disabled = false;
    banner(err.message, "error");
  }
}

const phaseLabels = {
  collect: "Lecture des playlists",
  target: "Préparation de la destination",
  match: "Recherche sur Apple Music",
  add: "Ajout des morceaux",
};

function updateProgress(job) {
  el("progress-msg").textContent = job.message || phaseLabels[job.phase] || "En cours…";
  const total = (job.progress && job.progress.total) || 0;
  const done = (job.progress && job.progress.done) || 0;
  let pct = total ? Math.round((done / total) * 100) : 5;
  if (job.phase === "target") pct = 100;
  el("progress-fill").style.width = Math.max(Math.min(pct, 100), 3) + "%";
  el("progress-detail").textContent = total ? `${done} / ${total}` : "";
}

function finishJob(job) {
  clearTimeout(state.jobTimer);
  el("progress-card").classList.add("hidden");
  el("start-btn").disabled = false;

  if (job.status === "error") {
    banner(job.error || "Erreur pendant l'opération.", "error");
    return;
  }
  renderResult(job);
}

function renderResult(job) {
  const result = job.result || {};
  const stats = result.stats || {};
  const cancelled = job.status === "cancelled";
  const isApple = result.provider === "apple_music";

  el("result-title").textContent = result.dry_run
    ? "Simulation terminée"
    : isApple
      ? "Transfert terminé"
      : "Fusion terminée";

  let sub = "";
  if (cancelled) sub = "Opération annulée. ";
  const destination = isApple ? " sur Apple Music" : "";
  if (result.dry_run) {
    sub += `${result.to_add} morceau(x) unique(s) seraient ajoutés à « ${result.playlist_name} »${destination}. Aucune modification n'a été effectuée.`;
  } else if (result.added != null) {
    sub += `${result.added} morceau(x) ajouté(s) à « ${result.playlist_name} »${destination}.`;
  }
  el("result-sub").textContent = sub;

  const rows = [[isApple ? "Morceaux à ajouter" : "Morceaux uniques retenus", result.to_add]];
  if (isApple) {
    rows.push(["Correspondances exactes (ISRC)", stats.matched_isrc]);
    rows.push(["Correspondances par recherche", stats.matched_search]);
    rows.push(["Introuvables sur Apple Music", stats.unmatched]);
    rows.push(["Doublons ignorés (entre sources)", stats.duplicates]);
  } else {
    rows.push(["Doublons ignorés (entre playlists)", stats.duplicates]);
  }
  rows.push(["Déjà dans la playlist cible", stats.already_in_target]);
  rows.push(["Épisodes ignorés", stats.episodes]);
  rows.push(["Fichiers locaux ignorés", stats.local_files]);
  rows.push(["Morceaux indisponibles", stats.unavailable]);
  if (!result.dry_run) rows.push(["Morceaux ajoutés", result.added]);

  el("result-stats").innerHTML = rows
    .map(
      ([label, value]) =>
        `<div class="stat"><div class="value">${escapeHtml(value == null ? 0 : value)}</div><div class="label">${escapeHtml(label)}</div></div>`
    )
    .join("");

  const linkWrap = el("result-link-wrap");
  if (result.playlist_url && !result.dry_run) {
    el("result-link").href = result.playlist_url;
    el("result-link").textContent = isApple
      ? "Ouvrir la playlist dans Apple Music"
      : "Ouvrir la playlist dans Spotify";
    linkWrap.classList.remove("hidden");
  } else {
    linkWrap.classList.add("hidden");
  }

  const dupDetails = el("dup-details");
  const dupPreview = result.dup_preview || [];
  if (dupPreview.length) {
    el("dup-list").innerHTML = dupPreview.map((label) => `<li>${escapeHtml(label)}</li>`).join("");
    dupDetails.classList.remove("hidden");
  } else {
    dupDetails.classList.add("hidden");
  }

  const unmatchedDetails = el("unmatched-details");
  const unmatchedPreview = result.unmatched_preview || [];
  if (unmatchedPreview.length) {
    el("unmatched-list").innerHTML = unmatchedPreview
      .map((label) => `<li>${escapeHtml(label)}</li>`)
      .join("");
    unmatchedDetails.classList.remove("hidden");
  } else {
    unmatchedDetails.classList.add("hidden");
  }

  const skipped = stats.skipped_playlists || [];
  const skippedWrap = el("skipped-wrap");
  if (skipped.length) {
    skippedWrap.textContent = "Playlists ignorées (lecture impossible) : " + skipped.join(", ");
    skippedWrap.classList.remove("hidden");
  } else {
    skippedWrap.classList.add("hidden");
  }

  el("result-card").classList.remove("hidden");
}

async function cancelJob() {
  if (!state.jobId) return;
  try {
    await api(`/api/merge/${state.jobId}/cancel`, { method: "POST" });
    el("progress-msg").textContent = "Annulation demandée…";
  } catch (err) {
    banner(err.message, "error");
  }
}

function resetMerge() {
  state.jobId = null;
  el("result-card").classList.add("hidden");
  el("progress-card").classList.add("hidden");
  el("progress-fill").style.width = "3%";
}

el("client-id-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveClientId("client-id-input");
});

el("client-id-form-2").addEventListener("submit", (event) => {
  event.preventDefault();
  saveClientId("client-id-input-2");
});

el("copy-btn").addEventListener("click", async () => {
  const text = el("redirect-uri-text").textContent;
  try {
    await navigator.clipboard.writeText(text);
    el("copy-btn").textContent = "Copié";
    setTimeout(() => (el("copy-btn").textContent = "Copier"), 2000);
  } catch (err) {
    banner("Copie impossible, sélectionnez le texte manuellement.", "error");
  }
});

el("logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  window.location.href = "/";
});

el("filter").addEventListener("input", renderPlaylists);

el("select-all").addEventListener("change", (event) => {
  if (event.target.checked) {
    state.playlists.forEach((p) => {
      if (p.can_read) state.selected.add(p.id);
    });
  } else {
    state.selected.clear();
  }
  renderPlaylists();
});

document.querySelectorAll('input[name="target-provider"]').forEach((radio) => {
  radio.addEventListener("change", syncProviderFields);
});

document.querySelectorAll('input[name="spotify-target-mode"]').forEach((radio) => {
  radio.addEventListener("change", () => syncTargetFields("spotify"));
});

document.querySelectorAll('input[name="apple-target-mode"]').forEach((radio) => {
  radio.addEventListener("change", () => syncTargetFields("apple"));
});

el("apple-config-form").addEventListener("submit", saveAppleConfig);
el("apple-key-file").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  try {
    el("apple-key-input").value = await file.text();
  } catch (err) {
    banner("Impossible de lire le fichier de clé.", "error");
  }
});
el("apple-connect-btn").addEventListener("click", connectAppleMusic);
el("apple-disconnect-btn").addEventListener("click", disconnectAppleMusic);
el("apple-reconfigure-btn").addEventListener("click", openAppleSetup);
el("apple-reconfigure-btn-2").addEventListener("click", openAppleSetup);

el("start-btn").addEventListener("click", startMerge);
el("cancel-btn").addEventListener("click", cancelJob);
el("new-merge-btn").addEventListener("click", resetMerge);

syncTargetFields("spotify");
syncTargetFields("apple");
renderAppleSection();
boot();
