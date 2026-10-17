// enTalkenator library web app: list, search, player, synced transcript, edits.
// No build step and no dependencies. Data comes from /app/api (same Worker).
"use strict";

(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* private mode */ } },
  };
  const SPEAKER_COLORS = ["#f2a541", "#3fb8af", "#e76f51", "#8e7dbe", "#5b8def", "#c45ab3", "#7fb069", "#d4a017", "#4cc9f0", "#ef476f"];
  const SVG_NS = "http://www.w3.org/2000/svg";

  // ---------------------------------------------------------------------------
  // DOM helpers (all text goes through textContent, never innerHTML)

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : String(kid));
    }
    return el;
  }

  function icon(d) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
    return svg;
  }
  const PLAY = "M8 5v14l11-7z";
  const PAUSE = "M6 5h4v14H6zM14 5h4v14h-4z";

  function fmtTime(s) {
    if (!Number.isFinite(s) || s < 0) s = 0;
    s = Math.floor(s);
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    return hh ? `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
  }
  function fmtLength(s) {
    if (!s) return "";
    const m = Math.round(s / 60);
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  }
  function fmtDate(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
  function fmtBytes(n) {
    if (n < 1e6) return `${Math.round(n / 1e3)} KB`;
    if (n < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
    return `${(n / 1e9).toFixed(2)} GB`;
  }

  let toastTimer = 0;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
  }

  // ---------------------------------------------------------------------------
  // API

  async function api(path, opts = {}) {
    const headers = { "X-Requested-With": "entalkenator" };
    if (opts.body) headers["Content-Type"] = "application/json";
    const res = await fetch("api/" + path, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (res.status === 401) {
      location.href = "login";
      throw new Error("Signed out");
    }
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  const state = {
    items: [],
    library: null,
    loadedAt: 0,
    filters: { q: "", template: "", tag: "" },
    transcripts: new Map(), // id -> {timed, turns}
    playing: null,          // the item in the player
    turns: null,            // its transcript turns, when timed
    currentTurn: -1,
    follow: store.get("ent:follow") !== "0",
  };

  async function loadItems() {
    const [items, library] = await Promise.all([api("items"), api("library")]);
    state.items = items.items;
    state.library = library;
    state.loadedAt = Date.now();
    $("#library-title").textContent = library.title;
    document.title = library.title;
  }

  function replaceItem(item) {
    const i = state.items.findIndex((x) => x.id === item.id);
    if (i >= 0) state.items[i] = item; else state.items.unshift(item);
    if (state.playing && state.playing.id === item.id) {
      state.playing = item;
      $("#player-title").textContent = item.title;
    }
  }

  function transcriptFor(id) {
    if (!state.transcripts.has(id)) {
      const p = api(`items/${encodeURIComponent(id)}/transcript`).catch((e) => {
        state.transcripts.delete(id);
        throw e;
      });
      state.transcripts.set(id, p);
    }
    return state.transcripts.get(id);
  }

  function speakerColors(turns, item) {
    const names = [];
    for (const s of item?.speakers || []) if (!names.includes(s.name)) names.push(s.name);
    for (const t of turns) if (!names.includes(t.speaker)) names.push(t.speaker);
    return new Map(names.map((n, i) => [n, SPEAKER_COLORS[i % SPEAKER_COLORS.length]]));
  }

  // ---------------------------------------------------------------------------
  // Positions (localStorage)

  function savedPosition(id) {
    try { return JSON.parse(store.get(`ent:pos:${id}`) || "null"); } catch { return null; }
  }
  function isDone(id) { return store.get(`ent:done:${id}`) === "1"; }

  // ---------------------------------------------------------------------------
  // Views

  function setNav(name) {
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  }

  function itemMeta(item) {
    const bits = [item.template, item.author && item.author !== "Unknown Author" ? item.author : "", fmtLength(item.duration), fmtDate(item.publishedAt || item.createdAt)];
    return bits.filter(Boolean).join(" · ");
  }

  function matches(item) {
    const f = state.filters;
    if (f.template && item.template !== f.template) return false;
    if (f.tag && !item.tags.some((t) => t.slug === f.tag)) return false;
    if (!f.q.trim()) return true;
    const hay = [item.title, item.author, item.template, item.description, ...item.tags.map((t) => t.name), ...item.speakers.map((s) => s.name)].join(" ").toLowerCase();
    return f.q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
  }

  function card(item) {
    const playing = state.playing && state.playing.id === item.id;
    const pos = savedPosition(item.id);
    const dur = item.duration || pos?.d || 0;
    const pct = isDone(item.id) ? 100 : pos && dur ? Math.min(100, (100 * pos.t) / dur) : 0;
    const bar = h("span");
    bar.style.width = `${pct}%`;
    const canPlay = !!item.files.audio;
    return h(
      "li",
      { class: `card${playing ? " playing" : ""}`, "data-id": item.id },
      h("button", {
        class: "play-btn", type: "button", disabled: !canPlay,
        "aria-label": canPlay ? `Play ${item.title}` : "No audio yet",
        onclick: () => playItem(item),
      }, icon(PLAY)),
      h("div", null,
        h("a", { class: "title", href: `#/item/${encodeURIComponent(item.id)}` }, item.title, item.status === "draft" ? h("span", { class: "badge", text: "Draft" }) : null),
        h("div", { class: "meta", text: itemMeta(item) }),
        item.tags.length ? h("div", { class: "tags" }, item.tags.map((t) => h("span", { class: "tag", text: t.name }))) : null,
        pct ? h("div", { class: "progress", title: isDone(item.id) ? "Played" : `${Math.round(pct)}% played` }, bar) : null,
      ),
    );
  }

  function renderLibrary() {
    setNav("library");
    const view = $("#view");
    const templates = [...new Set(state.items.map((i) => i.template).filter(Boolean))].sort();
    const tagCounts = new Map();
    for (const i of state.items) for (const t of i.tags) tagCounts.set(t.slug, { name: t.name, n: (tagCounts.get(t.slug)?.n || 0) + 1 });
    const list = h("ul", { class: "cards" });
    const count = h("p", { class: "count" });

    function update() {
      const shown = state.items.filter(matches);
      count.textContent = state.items.length ? `${shown.length} of ${state.items.length} programs` : "";
      list.replaceChildren(...shown.map(card));
      if (!state.items.length) {
        list.replaceChildren(h("li", { class: "empty" },
          h("strong", { text: "Your library is empty." }),
          h("p", { class: "muted" }, "Make a program with ", h("code", { text: "/entalkenator:entalkenate" }), " in Claude Code and choose “publish to your library” at the end, or run ", h("code", { text: "scripts/publish.py push <run dir>" }), "."),
        ));
      } else if (!shown.length) {
        list.replaceChildren(h("li", { class: "empty muted", text: "Nothing matches." }));
      }
    }

    const search = h("input", {
      type: "search", placeholder: "Search titles, authors, speakers, tags", "aria-label": "Search", value: state.filters.q,
      oninput: (e) => { state.filters.q = e.target.value; update(); },
    });
    const select = h("select", {
      "aria-label": "Template",
      onchange: (e) => { state.filters.template = e.target.value; update(); },
    }, h("option", { value: "", text: "All templates" }), templates.map((t) => h("option", { value: t, text: t, selected: t === state.filters.template })));
    const chips = h("div", { class: "chips", role: "group", "aria-label": "Tags" });
    function renderChips() {
      const chip = (slug, label) => h("button", {
        type: "button", class: "chip", "aria-pressed": String(state.filters.tag === slug),
        onclick: () => { state.filters.tag = slug; renderChips(); update(); },
      }, label);
      chips.replaceChildren(chip("", "All tags"), ...[...tagCounts].sort((a, b) => a[1].name.localeCompare(b[1].name)).map(([slug, t]) => chip(slug, `${t.name} (${t.n})`)));
    }
    renderChips();
    view.replaceChildren(
      h("div", { class: "filters" }, search, select),
      tagCounts.size ? chips : null,
      count,
      list,
    );
    update();
  }

  async function renderItem(id) {
    setNav("");
    const view = $("#view");
    let item = state.items.find((i) => i.id === id);
    if (!item) {
      try { item = (await api(`items/${encodeURIComponent(id)}`)).item; replaceItem(item); } catch (e) {
        view.replaceChildren(h("a", { class: "back", href: "#/", text: "← Library" }), h("p", { class: "notice", text: `Couldn't open this item: ${e.message}` }));
        return;
      }
    }
    const playBtn = h("button", { type: "button", class: "primary", disabled: !item.files.audio, onclick: () => playItem(item) },
      savedPosition(item.id) ? `Resume at ${fmtTime(savedPosition(item.id).t)}` : "Play");
    const actions = h("div", { class: "actions" },
      playBtn,
      h("button", { type: "button", onclick: () => rename(item) }, "Rename"),
      h("button", { type: "button", onclick: () => editTags(item) }, "Tags"),
      item.status === "draft"
        ? h("button", { type: "button", disabled: !item.files.audio, onclick: () => setStatus(item, "published") }, "Add to feeds")
        : h("button", { type: "button", onclick: () => setStatus(item, "draft") }, "Remove from feeds"),
      item.files.bundle ? h("a", { class: "button", href: `media/${encodeURIComponent(item.id)}/bundle.enTalkenator`, download: "" }, "Download .enTalkenator") : null,
      item.files.transcript ? h("a", { class: "button", href: `media/${encodeURIComponent(item.id)}/transcript.txt`, target: "_blank", rel: "noopener" }, "Transcript (.txt)") : null,
      h("button", { type: "button", class: "danger", onclick: () => remove(item) }, "Delete"),
    );
    const head = h("section", { class: "item-head" },
      h("h1", null, item.title, item.status === "draft" ? h("span", { class: "badge", text: "Draft" }) : null),
      h("div", { class: "meta muted", text: itemMeta(item) }),
      item.description ? h("p", { text: item.description }) : null,
      item.tags.length ? h("div", { class: "tags" }, item.tags.map((t) => h("span", { class: "tag", text: t.name }))) : null,
      actions,
    );
    const body = h("section", null, h("p", { class: "muted", text: "Loading transcript…" }));
    view.replaceChildren(h("a", { class: "back", href: "#/", text: "← Library" }), head, body);

    let t;
    try { t = await transcriptFor(item.id); } catch (e) {
      body.replaceChildren(h("p", { class: "notice", text: `Couldn't load the transcript: ${e.message}` }));
      return;
    }
    if (location.hash !== `#/item/${encodeURIComponent(item.id)}`) return; // navigated away
    if (!t.turns.length) {
      body.replaceChildren(h("p", { class: "muted", text: "No transcript was uploaded for this program." }));
      return;
    }
    const colors = speakerColors(t.turns, item);
    const legend = h("ul", { class: "legend", "aria-label": "Speakers" }, [...colors].map(([name, color]) => {
      const dot = h("span", { class: "dot" });
      dot.style.background = color;
      return h("li", { "data-speaker": name }, dot, name);
    }));
    const follow = h("input", {
      type: "checkbox", checked: state.follow,
      onchange: (e) => { state.follow = e.target.checked; store.set("ent:follow", state.follow ? "1" : "0"); },
    });
    const turns = h("ol", { class: "turns", "data-item": item.id }, t.turns.map((turn, i) => {
      const dot = h("span", { class: "dot" });
      dot.style.background = colors.get(turn.speaker);
      return h("li", { class: "turn", "data-i": i },
        h("div", { class: "who" }, dot, h("span", { text: turn.speaker }),
          t.timed ? h("button", { type: "button", title: "Play from here", "aria-label": `Play from ${fmtTime(turn.start)}`, onclick: () => playItem(item, turn.start) }, fmtTime(turn.start)) : null),
        h("p", { text: turn.text }));
    }));
    body.replaceChildren(
      legend,
      h("div", { class: "transcript-tools" },
        t.timed ? h("label", null, follow, "Follow along") : h("span", { class: "muted", text: "No chapter timings were uploaded, so the transcript can't follow the audio." }),
        h("span", { class: "muted", text: `${t.turns.length} turns` })),
      turns,
    );
    state.currentTurn = -1;
    syncTranscript(false);
  }

  async function renderFeeds() {
    setNav("feeds");
    const view = $("#view");
    view.replaceChildren(h("p", { class: "muted", text: "Loading…" }));
    let info;
    try { info = await api("library"); } catch (e) {
      view.replaceChildren(h("p", { class: "notice", text: e.message }));
      return;
    }
    const feedBox = (title, url, note) => {
      const input = h("input", { readonly: true, value: url, "aria-label": `${title} feed address`, onfocus: (e) => e.target.select() });
      return h("div", { class: "feed" },
        h("h3", { text: title }),
        note ? h("p", { class: "muted small", text: note }) : null,
        h("div", { class: "copy-row" }, input, h("button", { type: "button", onclick: () => copy(url) }, "Copy")),
        h("div", { class: "actions" },
          h("a", { class: "button", href: `overcast://x-callback-url/add?url=${encodeURIComponent(url)}` }, "Open in Overcast")));
    };
    const parts = [
      h("h1", { text: "Podcast feeds" }),
      h("p", { class: "notice" },
        "These addresses are private. Anyone who has one can listen, so don't post or share them, and never submit them to a podcast directory. ",
        "The feeds ask apps and search engines not to list them (", h("code", { text: "itunes:block" }), ", ", h("code", { text: "podcast:block" }), ", noindex)."),
    ];
    if (!info.feeds) {
      parts.push(h("p", { class: "notice", text: info.feedProblem || "Feeds are off." }));
    } else {
      parts.push(feedBox("Everything", info.feeds.all, `${info.published} published program${info.published === 1 ? "" : "s"}.`));
      if (info.feeds.tags.length) {
        parts.push(h("h2", { text: "One feed per tag" }));
        for (const t of info.feeds.tags) parts.push(feedBox(t.name, t.url, `${t.published} published.`));
      }
      parts.push(
        h("h2", { text: "Add a feed to your podcast app" }),
        h("ol", { class: "howto" },
          h("li", null, h("strong", { text: "Apple Podcasts" }), ": on iPhone or iPad, open Library, tap ", h("strong", { text: "…" }), " at the top, then ", h("strong", { text: "Follow a Show by URL" }), ". On a Mac, choose ", h("strong", { text: "File › Follow a Show by URL" }), ". Paste the address."),
          h("li", null, h("strong", { text: "Overcast" }), ": tap ", h("strong", { text: "Open in Overcast" }), " above, or tap + then ", h("strong", { text: "Add URL" }), ". Overcast keeps feeds marked ", h("code", { text: "itunes:block" }), " out of its search."),
          h("li", null, h("strong", { text: "Pocket Casts" }), ": go to ", h("a", { href: "https://pocketcasts.com/submit/", target: "_blank", rel: "noopener noreferrer", text: "pocketcasts.com/submit" }), ", paste the address and choose ", h("strong", { text: "Private" }), ", then search for the short link it gives you in the app. Without “Private”, Pocket Casts may list the show."),
        ),
      );
    }
    parts.push(h("p", { class: "muted small", text: `Storage used: ${fmtBytes(info.storageBytes)} (R2's free tier is 10 GB). ${info.items} item${info.items === 1 ? "" : "s"} in the library.` }));
    view.replaceChildren(...parts);
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied");
    } catch {
      toast("Select the address and copy it");
    }
  }

  // ---------------------------------------------------------------------------
  // Edits

  async function rename(item) {
    const title = prompt("New title", item.title);
    if (title === null || !title.trim() || title.trim() === item.title) return;
    try {
      replaceItem((await api(`items/${encodeURIComponent(item.id)}`, { method: "PATCH", body: { title: title.trim() } })).item);
      toast("Renamed");
      route();
    } catch (e) { toast(e.message); }
  }

  async function editTags(item) {
    const value = prompt("Tags, separated by commas. Each tag gets its own podcast feed.", item.tags.map((t) => t.name).join(", "));
    if (value === null) return;
    const tags = value.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      replaceItem((await api(`items/${encodeURIComponent(item.id)}`, { method: "PATCH", body: { tags } })).item);
      toast("Tags saved");
      route();
    } catch (e) { toast(e.message); }
  }

  async function setStatus(item, status) {
    try {
      replaceItem((await api(`items/${encodeURIComponent(item.id)}`, { method: "PATCH", body: { status } })).item);
      toast(status === "published" ? "Added to your feeds" : "Removed from your feeds");
      route();
    } catch (e) { toast(e.message); }
  }

  async function remove(item) {
    if (!confirm(`Delete “${item.title}”?\n\nThis removes its audio, transcript and bundle from your library and its feeds. It can't be undone.`)) return;
    try {
      await api(`items/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      state.items = state.items.filter((i) => i.id !== item.id);
      state.transcripts.delete(item.id);
      store.del(`ent:pos:${item.id}`);
      store.del(`ent:done:${item.id}`);
      if (state.playing && state.playing.id === item.id) closePlayer();
      toast("Deleted");
      location.hash = "#/";
    } catch (e) { toast(e.message); }
  }

  // ---------------------------------------------------------------------------
  // Player

  const audio = $("#audio");
  const player = $("#player");
  const seek = $("#seek");
  const speedSel = $("#speed");
  let pendingSeek = null;
  let dragging = false;
  let lastSave = 0;

  function speed() {
    const v = Number(store.get("ent:speed") || "1");
    return v >= 0.5 && v <= 3 ? v : 1;
  }
  speedSel.value = String(speed());
  if (!speedSel.value) speedSel.value = "1";

  function audioUrl(item) {
    const name = item.files.audio && item.files.audio.contentType === "audio/mpeg" ? "audio.mp3" : "audio.m4a";
    return `media/${encodeURIComponent(item.id)}/${name}`;
  }

  function load(item, startAt) {
    savePosition();
    state.playing = item;
    state.turns = null;
    state.currentTurn = -1;
    audio.src = audioUrl(item);
    audio.defaultPlaybackRate = audio.playbackRate = speed();
    const pos = savedPosition(item.id);
    pendingSeek = startAt ?? (pos ? Math.max(0, pos.t - 2) : null);
    $("#player-title").textContent = item.title;
    $("#player-speaker").textContent = "";
    player.hidden = false;
    store.set("ent:last", item.id);
    mediaSession(item);
    transcriptFor(item.id).then((t) => {
      if (state.playing && state.playing.id === item.id) {
        state.turns = t.timed ? t.turns : null;
        syncTranscript(false);
      }
    }).catch(() => {});
    document.querySelectorAll(".card").forEach((c) => c.classList.toggle("playing", c.dataset.id === item.id));
  }

  function playItem(item, startAt) {
    if (!item.files.audio) return;
    if (!state.playing || state.playing.id !== item.id) load(item, startAt);
    else if (startAt !== undefined) audio.currentTime = startAt;
    audio.play().catch((e) => toast(`Can't play: ${e.message}`));
  }

  function closePlayer() {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    state.playing = null;
    state.turns = null;
    player.hidden = true;
    store.del("ent:last");
  }

  function savePosition() {
    if (!state.playing || !Number.isFinite(audio.duration) || !audio.duration) return;
    lastSave = Date.now();
    const id = state.playing.id;
    const t = audio.currentTime;
    if (audio.duration - t < 15) {
      store.del(`ent:pos:${id}`);
      store.set(`ent:done:${id}`, "1");
    } else if (t > 5) {
      store.set(`ent:pos:${id}`, JSON.stringify({ t: Math.round(t), d: Math.round(audio.duration) }));
    }
  }

  function updateTimes() {
    const d = audio.duration;
    if (!dragging) seek.value = String(audio.currentTime);
    $("#time-cur").textContent = fmtTime(dragging ? Number(seek.value) : audio.currentTime);
    $("#time-left").textContent = Number.isFinite(d) ? `-${fmtTime(d - (dragging ? Number(seek.value) : audio.currentTime))}` : "";
  }

  audio.addEventListener("loadedmetadata", () => {
    seek.max = String(audio.duration || 0);
    if (pendingSeek !== null && pendingSeek < audio.duration) audio.currentTime = pendingSeek;
    pendingSeek = null;
    audio.playbackRate = speed();
    updateTimes();
  });
  audio.addEventListener("durationchange", () => { seek.max = String(audio.duration || 0); });
  audio.addEventListener("timeupdate", () => {
    updateTimes();
    syncTranscript(true);
    if (Date.now() - lastSave > 5000) savePosition();
  });
  audio.addEventListener("play", () => { $("#playpause").classList.add("playing"); $("#playpause").setAttribute("aria-label", "Pause"); });
  audio.addEventListener("pause", () => { $("#playpause").classList.remove("playing"); $("#playpause").setAttribute("aria-label", "Play"); savePosition(); });
  audio.addEventListener("ended", () => { savePosition(); });
  audio.addEventListener("error", () => { if (audio.getAttribute("src")) toast("The audio couldn't be loaded."); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) savePosition(); else if (Date.now() - state.loadedAt > 60000) refresh(); });
  window.addEventListener("pagehide", savePosition);

  seek.addEventListener("input", () => { dragging = true; updateTimes(); });
  seek.addEventListener("change", () => { dragging = false; audio.currentTime = Number(seek.value); });
  $("#playpause").addEventListener("click", () => (audio.paused ? audio.play().catch((e) => toast(e.message)) : audio.pause()));
  $("#back").addEventListener("click", () => { audio.currentTime = Math.max(0, audio.currentTime - 15); });
  $("#fwd").addEventListener("click", () => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + 30); });
  $("#prev-turn").addEventListener("click", () => jumpTurn(-1));
  $("#next-turn").addEventListener("click", () => jumpTurn(1));
  $("#player-title").addEventListener("click", () => { if (state.playing) location.hash = `#/item/${encodeURIComponent(state.playing.id)}`; });
  speedSel.addEventListener("change", () => {
    const v = Number(speedSel.value);
    store.set("ent:speed", String(v));
    audio.defaultPlaybackRate = audio.playbackRate = v;
  });

  document.addEventListener("keydown", (e) => {
    if (!state.playing || e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea", "button", "a"].includes(tag)) return;
    if (e.key === " ") { e.preventDefault(); $("#playpause").click(); }
    else if (e.key === "ArrowLeft") $("#back").click();
    else if (e.key === "ArrowRight") $("#fwd").click();
  });

  // Keep the page's bottom padding equal to the player's height.
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--player-h", player.hidden ? "0px" : `${player.offsetHeight}px`);
  }).observe(player);

  function turnAt(t) {
    const turns = state.turns;
    if (!turns || !turns.length) return -1;
    let lo = 0, hi = turns.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (turns[mid].start <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  function jumpTurn(dir) {
    const turns = state.turns;
    if (!turns) { audio.currentTime = Math.max(0, audio.currentTime + (dir < 0 ? -15 : 30)); return; }
    const i = turnAt(audio.currentTime);
    let target;
    if (dir < 0) target = i >= 0 && audio.currentTime - turns[i].start > 3 ? i : Math.max(0, i - 1);
    else target = Math.min(turns.length - 1, i + 1);
    audio.currentTime = turns[target].start;
    if (audio.paused) audio.play().catch(() => {});
  }

  /** Highlight the current turn and speaker; scroll to it when following along. */
  function syncTranscript(fromPlayback) {
    const i = state.playing ? turnAt(audio.currentTime) : -1;
    const changed = i !== state.currentTurn;
    state.currentTurn = i;
    const speaker = i >= 0 ? state.turns[i].speaker : "";
    $("#player-speaker").textContent = speaker;
    $("#prev-turn").disabled = $("#next-turn").disabled = !state.turns;
    const list = state.playing ? document.querySelector(`.turns[data-item="${CSS.escape(state.playing.id)}"]`) : null;
    document.querySelectorAll(".turn.current").forEach((el) => { if (!list || el.dataset.i !== String(i)) el.classList.remove("current"); });
    document.querySelectorAll(".legend li.speaking").forEach((el) => el.classList.remove("speaking"));
    if (!list || i < 0) return;
    const el = list.querySelector(`.turn[data-i="${i}"]`);
    if (el) el.classList.add("current");
    const leg = document.querySelector(`.legend li[data-speaker="${CSS.escape(speaker)}"]`);
    if (leg) leg.classList.add("speaking");
    if (el && changed && fromPlayback && state.follow && !audio.paused) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  function mediaSession(item) {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: item.title,
      artist: [item.template, item.author && item.author !== "Unknown Author" ? item.author : ""].filter(Boolean).join(" · "),
      album: state.library ? state.library.title : "enTalkenator",
      artwork: [{ src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }],
    });
    const set = (action, fn) => { try { navigator.mediaSession.setActionHandler(action, fn); } catch { /* unsupported */ } };
    set("play", () => audio.play());
    set("pause", () => audio.pause());
    set("seekbackward", (d) => { audio.currentTime = Math.max(0, audio.currentTime - (d.seekOffset || 15)); });
    set("seekforward", (d) => { audio.currentTime = Math.min(audio.duration || 0, audio.currentTime + (d.seekOffset || 30)); });
    set("seekto", (d) => { audio.currentTime = d.seekTime; });
    set("previoustrack", () => jumpTurn(-1));
    set("nexttrack", () => jumpTurn(1));
  }

  // ---------------------------------------------------------------------------
  // Routing and start-up

  function route() {
    const hash = location.hash || "#/";
    if (hash.startsWith("#/item/")) renderItem(decodeURIComponent(hash.slice("#/item/".length)));
    else if (hash === "#/feeds") renderFeeds();
    else renderLibrary();
  }

  async function refresh() {
    try {
      await loadItems();
      if (!location.hash.startsWith("#/item/") && location.hash !== "#/feeds") renderLibrary();
    } catch { /* keep what we have */ }
  }

  window.addEventListener("hashchange", () => { route(); window.scrollTo(0, 0); });

  (async () => {
    try {
      await loadItems();
    } catch (e) {
      $("#view").replaceChildren(h("p", { class: "notice", text: `Couldn't load the library: ${e.message}` }));
      return;
    }
    const last = state.items.find((i) => i.id === store.get("ent:last"));
    if (last && last.files.audio) load(last); // ready to resume, not playing
    route();
  })();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => { /* the app works without it */ });
  }
})();
