(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* private mode */ } },
  };

  let token = store.get("deck_token") || "";
  let config = null;
  let started = false;

  // ------------------------------------------------------------ plumbing

  async function api(path, opts = {}) {
    const headers = { "X-Deck-Token": token };
    let body = opts.body;
    if (body instanceof Blob) {
      headers["Content-Type"] = body.type || "audio/mp4";
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, { method: body !== undefined ? "POST" : "GET", headers, body });
    } catch (e) {
      setConn(false);
      throw new Error("Can't reach the Mac");
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (res.status === 401 && path !== "/api/login") {
      showLock();
      throw new Error("Signed out");
    }
    setConn(true);
    if (data.locked) { showLocked(true); clearTimeout(lockPoll); lockPoll = setTimeout(loadStatus, 4000); }
    if (!res.ok) throw new Error(data.error || "The Mac did not respond");
    return data;
  }

  let toastTimer;
  function toast(msg, isError) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("error", !!isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), isError ? Math.max(4200, msg.length * 70) : 2200);
  }

  function showLocked(locked) {
    $("#lockedTag").hidden = !locked;
    $(".mac-status").classList.toggle("is-locked", locked);
  }

  function setConn(ok) {
    $("#connDot").classList.toggle("ok", ok);
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function every(ms, fn) {
    fn();
    return setInterval(() => { if (!document.hidden) fn(); }, ms);
  }

  // ------------------------------------------------------------ PIN screen

  let pin = "";

  function buildPinpad() {
    const pad = $("#pinpad");
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "Clear", "0", "Enter"];
    pad.innerHTML = keys.map((k) =>
      `<button data-k="${k}" class="${k.length > 1 ? "text" : ""}">${k}</button>`).join("");
    pad.addEventListener("click", (e) => {
      const k = e.target.closest("button")?.dataset.k;
      if (!k) return;
      if (k === "Clear") pin = "";
      else if (k === "Enter") return submitPin();
      else if (pin.length < 12) pin += k;
      drawDots();
    });
  }

  function drawDots() {
    $$("#pinDots i").forEach((d, i) => d.classList.toggle("on", i < Math.min(pin.length, 6)));
  }

  async function submitPin() {
    if (pin.length < 4) return;
    try {
      const r = await api("/api/login", { body: { pin } });
      token = r.token;
      store.set("deck_token", token);
      pin = "";
      drawDots();
      $("#lockError").textContent = "";
      start();
    } catch (e) {
      pin = "";
      drawDots();
      $("#lockError").textContent = e.message;
      const box = $(".lock-inner");
      box.classList.remove("shake");
      void box.offsetWidth;
      box.classList.add("shake");
    }
  }

  function showLock() {
    token = "";
    store.del("deck_token");
    $("#app").hidden = true;
    $("#lock").hidden = false;
  }

  // ------------------------------------------------------------ icons

  const G = {
    lock: "M7 10V7a5 5 0 0110 0v3h1a1 1 0 011 1v9a1 1 0 01-1 1H6a1 1 0 01-1-1v-9a1 1 0 011-1zm2 0h6V7a3 3 0 00-6 0z",
    moon: "M20 14.5A8.5 8.5 0 019.5 4a8.5 8.5 0 1010.5 10.5z",
    shot: "M4 7h3l2-2h6l2 2h3a1 1 0 011 1v11a1 1 0 01-1 1H4a1 1 0 01-1-1V8a1 1 0 011-1zm8 3a4 4 0 100 8 4 4 0 000-8z",
    grid: "M3 3h8v8H3zm10 0h8v8h-8zM3 13h8v8H3zm10 0h8v8h-8z",
    voldown: "M3 9h4l5-4v14l-5-4H3zM15 11h7v2h-7z",
    volup: "M3 9h4l5-4v14l-5-4H3zM17.5 8h2v3h3v2h-3v3h-2v-3h-3v-2h3z",
    mute: "M3 9h4l5-4v14l-5-4H3zm12.4.4l1.4-1.4 2.2 2.2 2.2-2.2 1.4 1.4-2.2 2.2 2.2 2.2-1.4 1.4-2.2-2.2-2.2 2.2-1.4-1.4 2.2-2.2z",
    mic: "M12 3a3 3 0 013 3v6a3 3 0 01-6 0V6a3 3 0 013-3zM6 11h2a4 4 0 008 0h2a6 6 0 01-5 5.9V20h3v2H8v-2h3v-3.1A6 6 0 016 11z",
    search: "M10 3a7 7 0 015.6 11.2l5.1 5.1-1.4 1.4-5.1-5.1A7 7 0 1110 3zm0 2a5 5 0 100 10 5 5 0 000-10z",
    bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
    layers: "M12 3l10 5-10 5L2 8zm-7.6 8.8L12 15.6l7.6-3.8L22 13l-10 5-10-5z",
    keys: "M3 6h18a1 1 0 011 1v10a1 1 0 01-1 1H3a1 1 0 01-1-1V7a1 1 0 011-1zm2 3v2h2V9zm4 0v2h2V9zm4 0v2h2V9zm4 0v2h2V9zM7 13v2h10v-2z",
    video: "M3 7h12a1 1 0 011 1v2.5l5-3v9l-5-3V16a1 1 0 01-1 1H3a1 1 0 01-1-1V8a1 1 0 011-1z",
    hand: "M8 11V5a1.5 1.5 0 013 0v5h1V3.5a1.5 1.5 0 013 0V10h1V5a1.5 1.5 0 013 0v9a7 7 0 01-7 7h-1a6 6 0 01-5-2.7l-3-4.6a1.4 1.4 0 012.2-1.7L8 14z",
    hangup: "M12 9c-3.2 0-6.2.8-8.8 2.3-.6.3-.9 1-.7 1.6l.8 2.4c.2.6.8.9 1.4.8l3-.6c.5-.1.9-.5.9-1v-1.6a12 12 0 016.8 0V15c0 .5.4.9.9 1l3 .6c.6.1 1.2-.2 1.4-.8l.8-2.4c.2-.6-.1-1.3-.7-1.6A17 17 0 0012 9z",
    focus: "M12 2a10 10 0 110 20 10 10 0 010-20zm0 4a6 6 0 100 12 6 6 0 000-12zm0 3a3 3 0 110 6 3 3 0 010-6z",
    play: "M8 5v14l11-7z",
    pause: "M6 5h4v14H6zm8 0h4v14h-4z",
  };

  function glyphKey(tile) {
    const id = tile.id.toLowerCase();
    if (id.includes("camera")) return "video";
    if (id.includes("leave")) return "hangup";
    if (id.includes("hand")) return "hand";
    if (id.startsWith("focus")) return "focus";
    const b = {
      lock: "lock", display_sleep: "moon", screenshot: "shot", mission_control: "grid",
      vol_down: "voldown", vol_up: "volup", mute_toggle: "mute", mic_toggle: "mic", spotlight: "search",
    }[tile.builtin];
    if (b) return b;
    return { shortcut: "bolt", steps: "layers", keys: "keys", applescript: "bolt" }[tile.kind] || "bolt";
  }

  const svg = (key, cls = "glyph") => `<svg class="${cls}" viewBox="0 0 24 24"><path d="${G[key]}"/></svg>`;

  // ------------------------------------------------------------ tiles

  function renderTiles() {
    $$("[data-section]").forEach((box) => {
      const section = box.dataset.section;
      const tiles = config.sections[section] || [];
      const compact = box.classList.contains("wide") || box.classList.contains("strip");
      box.innerHTML = "";
      tiles.forEach((tile) => box.appendChild(makeTile(tile, compact, section)));
      const block = box.closest(".block");
      if (block) block.hidden = tiles.length === 0;
    });
  }

  function makeTile(tile, compact, section) {
    const el = document.createElement("button");
    el.className = "tile";
    el.dataset.id = tile.id;
    el.dataset.label = tile.label;
    if (tile.builtin === "mic_toggle") el.dataset.mic = "1";

    if (compact) {
      el.innerHTML = `${svg(glyphKey(tile))}<span class="lbl">${esc(tile.label)}</span>`;
    } else {
      let icon;
      if (tile.icon) {
        icon = `<img alt="" class="${section === "sites" && !tile.iv ? "site" : ""}" src="/api/icon/${encodeURIComponent(tile.id)}?t=${encodeURIComponent(token)}&v=${encodeURIComponent(tile.iv || "")}">`;
      } else if (tile.kind === "builtin") {
        icon = `<div class="mono">${svg(glyphKey(tile), "glyph")}</div>`;
      } else {
        icon = `<div class="mono">${esc(tile.label.trim().charAt(0).toUpperCase())}</div>`;
      }
      el.innerHTML = `<div class="icon">${icon}</div><span class="lbl">${esc(tile.label)}</span>`;
      const img = el.querySelector("img");
      if (img) {
        img.onerror = () => {
          img.parentNode.innerHTML = `<div class="mono">${esc(tile.label.trim().charAt(0).toUpperCase())}</div>`;
        };
      }
    }
    attachPress(el, tile);
    return el;
  }

  function attachPress(el, tile) {
    let holdTimer = null;
    let held = false;
    let confirmTimer = null;
    const canQuit = tile.kind === "open_app";

    el.addEventListener("pointerdown", () => {
      held = false;
      el.classList.add("pressed");
      if (canQuit) {
        holdTimer = setTimeout(() => {
          held = true;
          el.classList.remove("pressed");
          el.classList.add("confirm");
          el.querySelector(".lbl").textContent = `Quit ${tile.label}?`;
          if (navigator.vibrate) navigator.vibrate(20);
          clearTimeout(confirmTimer);
          confirmTimer = setTimeout(() => resetConfirm(el, tile), 3000);
        }, 600);
      }
    });
    const cancel = () => { clearTimeout(holdTimer); el.classList.remove("pressed"); };
    el.addEventListener("pointerleave", cancel);
    el.addEventListener("pointercancel", cancel);
    el.addEventListener("pointerup", () => {
      cancel();
      if (held) return;
      if (el.classList.contains("confirm")) {
        clearTimeout(confirmTimer);
        resetConfirm(el, tile);
        runTile(tile, el, "quit");
      } else {
        runTile(tile, el);
      }
    });
    el.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  function resetConfirm(el, tile) {
    el.classList.remove("confirm");
    el.querySelector(".lbl").textContent = tile.label;
  }

  async function runTile(tile, el, mode) {
    if (el.classList.contains("busy")) return;
    el.classList.add("busy");
    try {
      const r = await api("/api/action", { body: { id: tile.id, mode } });
      el.classList.remove("busy");
      flash(el, "done");
      if (r.toast) toast(r.toast);
      if (r.mic) showMic(r.mic);
    } catch (e) {
      el.classList.remove("busy");
      flash(el, "fail");
      toast(e.message, true);
    }
  }

  function flash(el, cls) {
    el.classList.add(cls);
    setTimeout(() => el.classList.remove(cls), cls === "done" ? 450 : 1200);
  }

  function showMic(state) {
    $$("[data-mic]").forEach((el) => {
      el.classList.toggle("alert", state === "muted");
      const lbl = el.querySelector(".lbl");
      if (!el.classList.contains("confirm")) lbl.textContent = state === "muted" ? "Mic muted" : el.dataset.label;
    });
  }

  // ------------------------------------------------------------ header

  function tickClock() {
    const now = new Date();
    $("#clockTime").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    $("#clockDate").textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  }

  let lockPoll = null;
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      $("#macName").textContent = s.mac || "Mac";
      $("#battery").textContent = s.battery == null ? "Power" : `${s.battery}%${s.charging ? " charging" : ""}`;
      if (s.mic && s.mic !== "unknown") showMic(s.mic);
      showLocked(!!s.locked);
      // while locked, check again soon so the tag clears right after unlocking
      clearTimeout(lockPoll);
      if (s.locked) lockPoll = setTimeout(loadStatus, 4000);
    } catch (e) { /* dot already shows it */ }
  }

  // ------------------------------------------------------------ calendar

  function dayLabel(d) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const that = new Date(d); that.setHours(0, 0, 0, 0);
    const diff = Math.round((that - today) / 86400000);
    if (diff === 0) return "Today";
    if (diff === 1) return "Tomorrow";
    return that.toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
  }

  function hm(d) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  async function loadAgenda(force) {
    const box = $("#agenda");
    try {
      const data = await api("/api/agenda" + (force ? "?force=1" : ""));
      if (data.error) {
        box.innerHTML = `<p class="muted small">${esc(data.error)}</p>`;
        return;
      }
      const events = data.events || [];
      if (!events.length) {
        box.innerHTML = `<p class="muted">Nothing on the calendar.</p>`;
        return;
      }
      const now = new Date();
      let html = "";
      let lastDay = "";
      for (const ev of events) {
        const s = new Date(ev.start), e = new Date(ev.end);
        const day = dayLabel(s);
        if (day !== lastDay) { html += `<div class="day">${day}</div>`; lastDay = day; }
        const past = e < now;
        const live = s <= now && now < e;
        const mins = Math.round((e - s) / 60000);
        const len = ev.allDay ? "All day" : mins >= 60 ? `${+(mins / 60).toFixed(1)} h` : `${mins} min`;
        const where = [len, ev.location].filter(Boolean).join(" · ");
        html += `<div class="event${past ? " past" : ""}${live ? " now" : ""}">
          <div class="when">${ev.allDay ? "All day" : hm(s)}</div>
          <div class="what"><div class="title">${esc(ev.title)}</div><div class="where">${esc(where)}</div></div>
          ${ev.canJoin && !past ? `<button class="join" data-join="${esc(ev.id)}">Join</button>` : ""}
        </div>`;
      }
      box.innerHTML = html;
    } catch (e) {
      box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`;
    }
  }

  $("#calRefresh").addEventListener("click", async () => {
    const b = $("#calRefresh");
    if (b.disabled) return;
    b.disabled = true;
    b.textContent = "Refreshing...";
    $("#agenda").style.opacity = "0.5";
    await loadAgenda(true);
    $("#agenda").style.opacity = "";
    b.textContent = "Updated";
    setTimeout(() => { b.textContent = "Refresh"; b.disabled = false; }, 1200);
  });

  $("#agenda").addEventListener("click", async (e) => {
    const id = e.target.closest("[data-join]")?.dataset.join;
    if (!id) return;
    try { toast((await api("/api/join", { body: { id } })).toast); } catch (err) { toast(err.message, true); }
  });

  // add event sheet
  let evDur = 30;
  const pad2 = (n) => String(n).padStart(2, "0");
  const localDate = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const localIso = (d) => `${localDate(d)}T${pad2(d.getHours())}:${pad2(d.getMinutes())}:00`;

  $("#addEventBtn").addEventListener("click", () => {
    const d = new Date();
    d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
    $("#evTitle").value = "";
    $("#evDate").value = localDate(d);
    $("#evTime").value = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    $("#eventSheet").hidden = false;
    setTimeout(() => $("#evTitle").focus(), 50);
  });
  $("#evCancel").addEventListener("click", () => ($("#eventSheet").hidden = true));
  $("#evDur").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    evDur = +b.dataset.d;
    $$("#evDur button").forEach((x) => x.classList.toggle("on", x === b));
  });
  $("#eventForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const start = new Date(`${$("#evDate").value}T${$("#evTime").value}:00`);
    const end = new Date(start.getTime() + evDur * 60000);
    try {
      await api("/api/event", { body: { title: $("#evTitle").value, start: localIso(start), end: localIso(end) } });
      $("#eventSheet").hidden = true;
      toast("Added to calendar");
      loadAgenda(true);
    } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ music

  let artPid = "";

  function renderMusic(m) {
    const playing = m.state === "playing";
    const has = m.name && m.state !== "off" && m.state !== "stopped";
    $("#trackName").textContent = has ? m.name : "Nothing playing";
    $("#trackArtist").textContent = has ? [m.artist, m.album].filter(Boolean).join(" · ") : "Apple Music";
    $("#trackBar").style.width = has && m.duration ? `${Math.min(100, (m.position / m.duration) * 100)}%` : "0";
    $("#playIcon").innerHTML = `<path d="${playing ? G.pause : G.play}"/>`;
    const art = $("#art");
    if (!has || !m.pid) {
      art.classList.remove("has-img");
      art.style.backgroundImage = "";
      artPid = "";
    } else if (m.pid !== artPid) {
      const url = `/api/art?pid=${m.pid}&t=${encodeURIComponent(token)}`;
      const img = new Image();
      img.onload = () => { art.style.backgroundImage = `url("${url}")`; art.classList.add("has-img"); artPid = m.pid; };
      img.src = url;
    }
  }

  async function loadMusic() {
    if ($('[data-page="home"]').hidden) return;
    try { renderMusic(await api("/api/nowplaying")); } catch (e) { /* ignore */ }
  }

  $$("[data-builtin]").forEach((b) => b.addEventListener("click", async () => {
    try { renderMusic(await api("/api/music", { body: { cmd: b.dataset.builtin } })); } catch (e) { toast(e.message, true); }
  }));

  // ------------------------------------------------------------ Claude

  async function claude(send) {
    const text = $("#claudeText").value.trim();
    if (!text) return toast("Type something first", true);
    try {
      const r = await api("/api/claude", { body: { text, send } });
      toast(r.toast);
      if (send) $("#claudeText").value = "";
    } catch (e) { toast(e.message, true); }
  }
  $("#claudePaste").addEventListener("click", () => claude(false));
  $("#claudeSend").addEventListener("click", () => claude(true));

  // ------------------------------------------------------------ clipboard

  async function copyHere(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied on iPad");
    } catch (e) {
      toast("iPad would not allow copying here", true);
    }
  }

  $("#clipSend").addEventListener("click", async () => {
    try { toast((await api("/api/clipboard", { body: { text: $("#clipText").value } })).toast); } catch (e) { toast(e.message, true); }
  });
  $("#clipGet").addEventListener("click", async () => {
    try {
      const r = await api("/api/clipboard");
      $("#clipText").value = r.text;
      toast(r.text ? "Got the Mac clipboard" : "Mac clipboard has no text");
    } catch (e) { toast(e.message, true); }
  });
  $("#clipCopyHere").addEventListener("click", () => copyHere($("#clipText").value));

  // ------------------------------------------------------------ voice notes

  let recorder = null, chunks = [], recStart = 0, recTick = null, recStream = null;
  const fmtDur = (s) => `${Math.floor(s / 60)}:${pad2(Math.floor(s % 60))}`;

  function pickMime() {
    if (!window.MediaRecorder) return null;
    for (const t of ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"]) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return "";
  }

  async function toggleRecord() {
    if (recorder && recorder.state === "recording") return recorder.stop();
    if (!window.isSecureContext || !navigator.mediaDevices || !window.MediaRecorder) {
      return toast("The microphone needs the certificate step. See setup.", true);
    }
    try {
      recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      return toast("Microphone access was blocked", true);
    }
    const mime = pickMime();
    recorder = new MediaRecorder(recStream, mime ? { mimeType: mime } : undefined);
    chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = uploadRecording;
    recorder.start(1000);
    recStart = Date.now();
    $("#recBtn").classList.add("on");
    $("#recState").textContent = "Recording. Tap to stop.";
    recTick = setInterval(() => {
      const s = (Date.now() - recStart) / 1000;
      $("#recTime").textContent = fmtDur(s);
      if (s > 1800) recorder.stop();
    }, 250);
  }

  async function uploadRecording() {
    clearInterval(recTick);
    recStream.getTracks().forEach((t) => t.stop());
    $("#recBtn").classList.remove("on");
    $("#recState").textContent = "Saving on Mac...";
    const type = (recorder.mimeType || "audio/mp4").split(";")[0];
    const blob = new Blob(chunks, { type });
    try {
      await api("/api/voice", { body: blob });
      $("#recState").textContent = "Saved. Tap to record another.";
      loadNotes();
    } catch (e) {
      $("#recState").textContent = "Not saved";
      toast(e.message, true);
    }
    $("#recTime").textContent = "0:00";
  }

  $("#recBtn").addEventListener("click", toggleRecord);

  let notesPoll = null;
  async function loadNotes() {
    try {
      const r = await api("/api/voice");
      $("#voiceHint").textContent = r.canTranscribe ? "" : "Transcription not set up";
      $("#notes").innerHTML = r.notes.map((n) => {
        const when = new Date(n.when).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
        let body = "";
        if (n.status === "transcribing") body = `<div class="text muted">Transcribing on the Mac...</div>`;
        else if (n.status === "audio") body = `<div class="text muted">Audio saved on the Mac.</div>`;
        else body = `<div class="text">${esc(n.text)}</div>
          <div class="row">
            <button class="ghost" data-use="claude" data-name="${esc(n.name)}">To Claude</button>
            <button class="ghost" data-use="clipboard" data-name="${esc(n.name)}">To Mac clipboard</button>
            <button class="ghost" data-copy="1">Copy here</button>
          </div>`;
        return `<div class="note"><div class="meta">${esc(when)}</div>${body}</div>`;
      }).join("") || `<p class="muted small">Notes save to a folder on your Mac.</p>`;
      const busy = r.notes.some((n) => n.status === "transcribing");
      clearTimeout(notesPoll);
      if (busy) notesPoll = setTimeout(loadNotes, 4000);
    } catch (e) { /* ignore */ }
  }

  $("#notes").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.copy) return copyHere(b.closest(".note").querySelector(".text").textContent);
    try {
      toast((await api("/api/voice/use", { body: { name: b.dataset.name, to: b.dataset.use } })).toast);
    } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ search

  let searchTimer;
  $("#searchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    searchTimer = setTimeout(async () => {
      if (q.length < 2) { $("#results").innerHTML = ""; return; }
      try {
        const r = await api("/api/search?q=" + encodeURIComponent(q));
        $("#results").innerHTML = r.results.map((x) => `
          <div class="result">
            <div class="name" data-open="${x.id}"><div>${esc(x.name)}</div><div class="folder">${esc(x.folder)}</div></div>
            <button class="ghost" data-open="${x.id}">Open</button>
            <button class="ghost" data-reveal="${x.id}">Show</button>
          </div>`).join("") || `<p class="muted small">No matches</p>`;
      } catch (err) { toast(err.message, true); }
    }, 350);
  });

  $("#results").addEventListener("click", async (e) => {
    const t = e.target.closest("[data-open],[data-reveal]");
    if (!t) return;
    const id = t.dataset.open || t.dataset.reveal;
    try { toast((await api("/api/open", { body: { id, reveal: !!t.dataset.reveal } })).toast); } catch (err) { toast(err.message, true); }
  });

  // ------------------------------------------------------------ timer

  let timer = JSON.parse(store.get("deck_timer") || "null") || { mins: 25, end: 0, left: 25 * 60 };
  let audioCtx = null;

  function saveTimer() { store.set("deck_timer", JSON.stringify(timer)); }

  function timerLeft() {
    return timer.end ? Math.max(0, Math.round((timer.end - Date.now()) / 1000)) : timer.left;
  }

  function drawTimer() {
    const left = timerLeft();
    $("#timerFace").textContent = fmtDur(left);
    $("#timerGo").textContent = timer.end ? "Pause" : left < timer.mins * 60 ? "Resume" : "Start";
    const mini = $("#miniTimer");
    mini.hidden = !timer.end;
    mini.textContent = fmtDur(left);
    if (timer.end && left === 0) {
      timer = { mins: timer.mins, end: 0, left: timer.mins * 60 };
      saveTimer();
      ring();
      drawTimer();
    }
  }

  function ring() {
    toast("Time is up");
    const card = $(".timer-card");
    card.classList.remove("ring");
    void card.offsetWidth;
    card.classList.add("ring");
    if (!audioCtx) return;
    [0, 0.45, 0.9].forEach((t) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.3, audioCtx.currentTime + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + t + 0.35);
      o.connect(g).connect(audioCtx.destination);
      o.start(audioCtx.currentTime + t);
      o.stop(audioCtx.currentTime + t + 0.4);
    });
  }

  $$(".presets button").forEach((b) => b.addEventListener("click", () => {
    timer = { mins: +b.dataset.min, end: 0, left: +b.dataset.min * 60 };
    saveTimer();
    drawTimer();
  }));
  $("#timerGo").addEventListener("click", () => {
    try { audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)(); audioCtx.resume(); } catch (e) { /* no sound */ }
    if (timer.end) timer = { mins: timer.mins, end: 0, left: timerLeft() };
    else timer = { mins: timer.mins, end: Date.now() + timer.left * 1000, left: timer.left };
    saveTimer();
    drawTimer();
  });
  $("#timerReset").addEventListener("click", () => {
    timer = { mins: timer.mins, end: 0, left: timer.mins * 60 };
    saveTimer();
    drawTimer();
  });

  // ------------------------------------------------------------ tabs and misc

  function showTab(name) {
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".page").forEach((p) => (p.hidden = p.dataset.page !== name));
    store.set("deck_tab", name);
    if (name === "home") loadMusic();
    if (name === "capture") loadNotes();
    $(".pages").scrollTop = 0;
  }
  $$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

  $("#reloadBtn").addEventListener("click", () => location.reload());
  $("#logoutBtn").addEventListener("click", async () => {
    try { await api("/api/logout", { body: {} }); } catch (e) { /* signing out anyway */ }
    showLock();
  });

  let wakeLock = null;
  async function keepAwake() {
    try { if ("wakeLock" in navigator && !document.hidden) wakeLock = await navigator.wakeLock.request("screen"); } catch (e) { /* not supported */ }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden || !started) return;
    keepAwake();
    loadStatus();
    loadAgenda();
    loadMusic();
  });

  // ------------------------------------------------------------ start

  async function start() {
    try {
      config = await api("/api/config");
    } catch (e) {
      if (!token) return;
      $("#lockError").textContent = `${e.message}. Trying again...`;
      $("#lock").hidden = false;
      setTimeout(start, 5000);
      return;
    }
    $("#lock").hidden = true;
    $("#app").hidden = false;
    renderTiles();
    showTab(store.get("deck_tab") || "home");
    if (started) return;
    started = true;
    setInterval(tickClock, 1000); tickClock();
    setInterval(drawTimer, 1000); drawTimer();
    every(15000, loadStatus);
    every(30000, () => loadAgenda());
    every(3000, loadMusic);
    keepAwake();
  }

  buildPinpad();
  if (token) start(); else showLock();
})();
