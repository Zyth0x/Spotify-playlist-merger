const state = {
  playlists: [],
  selected: new Set(),
  jobId: null,
  jobTimer: null,
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
    renderUser(data.me);
    showView("merger");
    renderTargetOptions();
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

function currentTargetMode() {
  return document.querySelector('input[name="target-mode"]:checked').value;
}

function syncTargetFields() {
  const mode = currentTargetMode();
  el("new-fields").classList.toggle("hidden", mode !== "new");
  el("existing-fields").classList.toggle("hidden", mode !== "existing");
}

async function startMerge() {
  const errorNode = el("start-error");
  errorNode.classList.add("hidden");
  const ids = Array.from(state.selected);
  if (!ids.length) {
    errorNode.textContent = "Sélectionnez au moins une playlist source.";
    errorNode.classList.remove("hidden");
    return;
  }
  const mode = currentTargetMode();
  let target;
  if (mode === "new") {
    target = {
      mode: "new",
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
    target = { mode: "existing", id: el("target-select").value };
    if (!target.id) {
      errorNode.textContent = "Choisissez une playlist existante.";
      errorNode.classList.remove("hidden");
      return;
    }
  }
  const options = {
    dedupe: el("opt-dedupe").checked,
    dedupe_isrc: el("opt-isrc").checked,
    dry_run: el("opt-dry").checked,
  };

  el("start-btn").disabled = true;
  el("result-card").classList.add("hidden");
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
    banner(job.error || "Erreur pendant la fusion.", "error");
    return;
  }
  renderResult(job);
}

function renderResult(job) {
  const result = job.result || {};
  const stats = result.stats || {};
  const cancelled = job.status === "cancelled";

  el("result-title").textContent = result.dry_run ? "Simulation terminée" : "Fusion terminée";
  let sub = "";
  if (cancelled) sub = "Fusion annulée. ";
  if (result.dry_run) {
    sub += `${result.to_add} morceau(x) unique(s) seraient ajoutés à « ${result.playlist_name} ». Aucune modification n'a été effectuée.`;
  } else if (result.added != null) {
    sub += `${result.added} morceau(x) ajouté(s) à « ${result.playlist_name} ».`;
  }
  el("result-sub").textContent = sub;

  const rows = [
    ["Morceaux uniques retenus", result.to_add],
    ["Doublons ignorés (entre playlists)", stats.duplicates],
    ["Déjà dans la playlist cible", stats.already_in_target],
    ["Épisodes ignorés", stats.episodes],
    ["Fichiers locaux ignorés", stats.local_files],
    ["Morceaux indisponibles", stats.unavailable],
  ];
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

document.querySelectorAll('input[name="target-mode"]').forEach((radio) => {
  radio.addEventListener("change", syncTargetFields);
});

el("start-btn").addEventListener("click", startMerge);
el("cancel-btn").addEventListener("click", cancelJob);
el("new-merge-btn").addEventListener("click", resetMerge);

syncTargetFields();
boot();
