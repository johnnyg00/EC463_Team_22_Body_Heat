/* Body Heat demo front end. Displays what the hub validated and the pod decided;
   never computes a temperature decision. Live updates arrive by SSE as tile diffs. */
(() => {
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const api = async (path, body) => {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.detail || j.error || r.statusText), { status: r.status, body: j });
    return j;
  };

  let me = null, strings = {}, tiles = new Map(), es = null, lastMsgAt = 0, screen = null, detailDev = null, detailTimer = null;

  // ---------- auth ----------
  async function boot() {
    const j = await api("/api/me");
    strings = j.strings; $("#intended-use").textContent = strings.intended_use;
    if (j.user) { me = j.user; enter(); return; }
    const ul = $("#account-list"); ul.innerHTML = "";
    j.accounts.filter(a => a.role === "staff" || ["athlete11", "athlete07", "athlete33"].includes(a.username))
      .forEach(a => { const li = el("li"); li.innerHTML = `<code>${a.username}</code> — ${a.display}`; ul.appendChild(li); });
  }
  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try { me = (await api("/api/login", { username: f.get("username"), password: f.get("password") })).user; enter(); }
    catch (e) { $("#login-err").textContent = "Sign-in failed."; }
  });
  $("#signout").addEventListener("click", async () => { await api("/api/logout", {}); location.reload(); });

  function enter() {
    $("#screen-login").classList.add("hidden"); $("#hdr").classList.remove("hidden");
    $("#who").textContent = `${me.display} · ${me.role}`;
    const nav = $("#nav"); nav.innerHTML = "";
    const screens = me.role === "staff" ? [["roster", "Roster"], ["session", "Session"], ["demo", "Demo controls"]] : [["athlete", "My session"]];
    screens.forEach(([id, label]) => { const b = el("button", "", label); b.dataset.screen = id; b.onclick = () => show(id); nav.appendChild(b); });
    show(screens[0][0]);
    connect();
  }
  function show(id) {
    screen = id;
    document.querySelectorAll(".screen").forEach(s => s.classList.toggle("hidden", s.id !== `screen-${id}`));
    document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("active", b.dataset.screen === id));
    if (id === "session") loadSession();
    if (id === "demo") loadDemo();
    if (id === "athlete") loadAthlete();
  }

  // ---------- live feed ----------
  function connect() {
    if (es) es.close();
    es = new EventSource("/api/events");
    es.onmessage = (ev) => {
      lastMsgAt = Date.now(); setLost(false);
      const m = JSON.parse(ev.data);
      $("#clock").textContent = `t = ${m.t} s`;
      if (m.full) tiles.clear();
      (m.tiles || []).forEach(t => tiles.set(t.device_id, t));
      if (m.tiles) renderGrid();
      if (m.strip) renderStrip(m.strip);
      if (m.notices) renderNotices(m.notices);
      if (m.status) renderStatus(m.status);
      if (me.role === "athlete" && m.tiles) renderAthleteTile();
    };
    es.onerror = () => { /* the 5 s watchdog below decides */ };
  }
  setInterval(() => { if (me && lastMsgAt && Date.now() - lastMsgAt > 5000) setLost(true); }, 1000);
  function setLost(lost) {
    document.body.classList.toggle("lost", lost);
    const b = $("#banner");
    if (lost) { b.textContent = strings.connection_lost; b.className = "banner lost"; }
    else if (!b.classList.contains("fault")) b.classList.add("hidden");
  }
  function renderStatus(s) {
    const b = $("#banner");
    if (s.storage_fault && !document.body.classList.contains("lost")) { b.textContent = `${strings.storage_fault}: ${s.storage_fault}`; b.className = "banner fault"; }
    else if (!document.body.classList.contains("lost")) { b.className = "banner hidden"; }
    window.__status = s;
    if (screen === "demo") renderCounters(s);
  }

  // ---------- roster ----------
  function tileNode(t, big) {
    const d = el("div", `tile ${t.state}${t.greyed ? " greyed" : ""}${big ? " big-tile" : ""}`);
    d.dataset.dev = t.device_id;
    const top = el("div", "top"); top.appendChild(el("span", "jersey", `#${t.jersey}`)); top.appendChild(el("span", "name", t.name)); d.appendChild(top);
    const temp = el("div", "temp");
    if (t.skin_c == null) { temp.classList.add("hidden-temp"); temp.textContent = t.alerts_only ? "alerts only" : "—"; }
    else { temp.textContent = `${t.skin_c.toFixed(1)} °C`; const a = el("span", "arrow", t.arrow); temp.appendChild(a); }
    d.appendChild(temp);
    const lab = el("div", "label"); lab.appendChild(el("span", "ico", t.icon)); lab.appendChild(el("span", "", t.label)); d.appendChild(lab);
    const foot = el("div", "foot");
    const badges = el("div", "badges"); t.badge_labels.forEach(b => badges.appendChild(el("span", "badge", b))); foot.appendChild(badges);
    const age = t.age_s == null ? "no data yet" : (t.state === "no_signal" ? `last ${t.age_s} s ago` : `${t.measurement_age_s ?? t.age_s} s`);
    foot.appendChild(el("span", "age mono", age)); d.appendChild(foot);
    if (!big) d.onclick = () => openDetail(t.device_id);
    return d;
  }
  function renderGrid() {
    const g = $("#grid");
    const order = [...tiles.values()].sort((a, b) => a.jersey - b.jersey);
    if (g.childElementCount !== order.length) { g.innerHTML = ""; order.forEach(t => g.appendChild(tileNode(t))); return; }
    order.forEach((t, i) => { const old = g.children[i]; const n = tileNode(t); if (old.outerHTML !== n.outerHTML) g.replaceChild(n, old); });
  }
  function renderStrip(rows) {
    const s = $("#strip"); s.innerHTML = "";
    if (!rows.length) { s.appendChild(el("div", "empty", `✓ ${strings.no_alerts}`)); return; }
    rows.forEach(r => {
      const d = el("div", `alert-row ${r.state}${r.acknowledged ? " acked" : ""}`);
      d.appendChild(el("span", "ico", r.icon));
      d.appendChild(el("span", "jersey", `#${r.jersey}`));
      d.appendChild(el("span", "", `${r.name} — ${r.label}`));
      if (r.acknowledged) d.appendChild(el("span", "pill", `acknowledged by ${r.ack_by} at t=${r.ack_t}s`));
      d.appendChild(el("span", "since", `${r.since_s} s`));
      if (!r.acknowledged) { const b = el("button", "btn small", strings.ack); b.title = strings.ack_note; b.onclick = (e) => { e.stopPropagation(); api("/api/ack", { device_id: r.device_id }); }; d.appendChild(b); }
      d.onclick = () => openDetail(r.device_id);
      s.appendChild(d);
    });
  }
  function renderNotices(ns) {
    const n = $("#notices"); n.innerHTML = "";
    ns.forEach(x => n.appendChild(el("span", `notice ${x.kind}`, `#${x.jersey} ${x.name}: ${x.text}`)));
  }

  // ---------- detail (staff) ----------
  async function openDetail(dev) {
    detailDev = dev; $("#modal").classList.remove("hidden");
    await refreshDetail();
    clearInterval(detailTimer); detailTimer = setInterval(refreshDetail, 2000);
  }
  async function refreshDetail() {
    if (!detailDev) return;
    const d = await api(`/api/wearer/${detailDev}`);
    $("#detail-title").textContent = `#${d.tile.jersey} ${d.tile.name} · ${detailDev} (${d.kind})`;
    const tt = $("#detail-tile"); tt.innerHTML = ""; tt.appendChild(tileNode(d.tile, true));
    const acts = $("#detail-actions"); acts.innerHTML = "";
    if (me.role === "staff") {
      if (["absolute", "trend"].includes(d.tile.state) && !d.tile.badges.includes("acknowledged")) {
        const b = el("button", "btn", strings.ack); b.title = strings.ack_note; b.onclick = async () => { await api("/api/ack", { device_id: detailDev }); refreshDetail(); }; acts.appendChild(b);
      }
      const ao = el("button", `btn${d.alerts_only ? " on" : ""}`, d.alerts_only ? "Alerts only: on" : "Alerts only: off");
      ao.onclick = async () => { await api("/api/alerts_only", { device_id: detailDev, on: !d.alerts_only }); refreshDetail(); }; acts.appendChild(ao);
      const c = el("button", "btn", d.consented ? strings.withdraw : "Record consent");
      c.onclick = async () => { await api("/api/consent", { device_id: detailDev, consented: !d.consented }); refreshDetail(); }; acts.appendChild(c);
      acts.appendChild(el("span", "tiny muted", strings.ack_note));
    }
    const ev = $("#detail-events"); ev.innerHTML = "";
    d.events.forEach(e => { const r = el("div"); r.appendChild(el("span", "t mono", `t=${e.t}s`)); r.appendChild(el("span", "k", e.kind)); r.appendChild(el("span", "", `${e.detail || ""}${e.user ? " · " + e.user : ""}`)); ev.appendChild(r); });
    $("#detail-chart").innerHTML = ""; $("#detail-chart").appendChild(chart(d, 820, 460));
  }
  $("#detail-close").onclick = () => { $("#modal").classList.add("hidden"); detailDev = null; clearInterval(detailTimer); };

  // ---------- chart (bundled, no CDN): skin + air (°C), humidity (%), motion; alert markers ----------
  function chart(d, W, H) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "chart");
    const s = d.series, skin = s.skin_c || [], air = s.air_c || [], hum = s.humidity_pct || [], mot = s.motion || [];
    const all = [...skin, ...air, ...hum, ...mot];
    if (!all.length) { const t = document.createElementNS(svg.namespaceURI, "text"); t.setAttribute("x", 20); t.setAttribute("y", 30); t.textContent = "No stored readings yet"; svg.appendChild(t); return svg; }
    const x0 = Math.min(...all.map(p => p[0])), x1 = Math.max(d.t, ...all.map(p => p[0]));
    const L = 48, R = 14, panels = [
      { title: "Skin and air temperature (°C)", h: 0.55, series: [["skin", skin], ["air", air]], limit: 38.5 },
      { title: "Humidity (%RH)", h: 0.2, series: [["hum", hum]] },
      { title: "Motion (g rms)", h: 0.25, series: [["mot", mot]] },
    ];
    const ns = svg.namespaceURI, mk = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
    const X = (t) => L + (t - x0) / Math.max(1, x1 - x0) * (W - L - R);
    let top = 10;
    panels.forEach(p => {
      const ph = (H - 40) * p.h, bottom = top + ph - 18;
      const vals = p.series.flatMap(([, pts]) => pts.map(q => q[1])).concat(p.limit ? [p.limit] : []);
      let lo = Math.min(...vals), hi = Math.max(...vals); if (hi - lo < 0.5) { hi += 0.25; lo -= 0.25; } const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
      const Y = (v) => bottom - (v - lo) / (hi - lo) * (bottom - top - 14);
      svg.appendChild(mk("line", { x1: L, x2: W - R, y1: bottom, y2: bottom, class: "axis" }));
      svg.appendChild(mk("line", { x1: L, x2: L, y1: top + 14, y2: bottom, class: "axis" }));
      const title = mk("text", { x: L + 4, y: top + 10 }); title.textContent = p.title; svg.appendChild(title);
      [lo + pad, hi - pad].forEach(v => { const t = mk("text", { x: 4, y: Y(v) + 4 }); t.textContent = v.toFixed(1); svg.appendChild(t); });
      if (p.limit) { svg.appendChild(mk("line", { x1: L, x2: W - R, y1: Y(p.limit), y2: Y(p.limit), class: "limit" })); const t = mk("text", { x: W - R - 150, y: Y(p.limit) - 4 }); t.textContent = "team setting 38.5"; svg.appendChild(t); }
      p.series.forEach(([cls, pts]) => { if (!pts.length) return; svg.appendChild(mk("path", { class: cls, d: pts.map((q, i) => `${i ? "L" : "M"}${X(q[0]).toFixed(1)},${Y(q[1]).toFixed(1)}`).join(" ") })); });
      d.events.forEach(e => {
        const cls = e.kind === "alert" ? (e.detail === "absolute" ? "mark-absolute" : "mark-alert") : e.kind === "alert_clear" ? "mark-clear" : e.kind === "ack" ? "mark-ack" : e.kind === "no_signal" ? "mark-no_signal" : null;
        if (cls && e.t >= x0) svg.appendChild(mk("line", { x1: X(e.t), x2: X(e.t), y1: top + 14, y2: bottom, class: cls }));
      });
      top += ph;
    });
    [x0, x1].forEach(t => { const tx = mk("text", { x: X(t) - (t === x1 ? 36 : 0), y: H - 6 }); tx.textContent = `t=${t}s`; svg.appendChild(tx); });
    const wrap = el("div"); wrap.appendChild(svg);
    const lg = el("div", "legend"); lg.innerHTML = `<span><i style="border-color:var(--accent)"></i>skin</span><span><i style="border-color:#8fd3ff"></i>air</span><span><i style="border-color:var(--det)"></i>humidity</span><span><i style="border-color:var(--ok)"></i>motion</span><span><i style="border-color:var(--trend)"></i>trend alert</span><span><i style="border-color:var(--abs)"></i>above setting</span><span><i style="border-color:var(--ok);border-style:dashed"></i>pod cleared</span><span><i style="border-color:var(--fg);border-style:dotted"></i>acknowledged</span>`;
    wrap.appendChild(lg);
    return wrap;
  }

  // ---------- session screen ----------
  async function loadSession() {
    const s = await api("/api/session");
    $("#session-info").innerHTML = `<table class="kv"><tr><td>Session</td><td>${s.session_id}</td></tr><tr><td>Started</td><td>${s.started}</td></tr><tr><td>Wearers</td><td>${s.wearers.length} (${s.wearers.filter(w => w.kind === "pod").length} pods + ${s.wearers.filter(w => w.kind !== "pod").length} emulated)</td></tr><tr><td>Elapsed</td><td class="mono">${s.t} s</td></tr></table>`;
    const names = { trend_limit_c_per_5min: "Trend limit (°C per 5 min)", trend_window_s: "Trend window (s, demo-shortened)", absolute_limit_c: "Absolute limit (°C)", recovery_period_s: "Recovery period (s)", low_battery_pct: "Low-battery flag (%)", no_signal_after_s: "No signal after (s)" };
    $("#settings").innerHTML = Object.entries(s.settings).map(([k, v]) => `<tr><td>${names[k] || k}</td><td class="mono">${v}</td></tr>`).join("");
    const w = $("#wearers");
    w.innerHTML = `<tr><th>#</th><th>Name</th><th>Device</th><th>Source</th><th>Consent</th><th>Alerts only</th></tr>` + s.wearers.map(x => `<tr><td><b>${x.jersey}</b></td><td>${x.full_name}</td><td class="mono">${x.device_id}</td><td><span class="pill ${x.kind}">${x.kind}</span></td>
      <td><button class="btn small ${x.consented ? "on" : ""}" data-consent="${x.device_id}" data-val="${x.consented ? 0 : 1}">${x.consented ? "Consented · withdraw" : "Not collecting · record consent"}</button></td>
      <td><button class="btn small ${x.alerts_only ? "on" : ""}" data-ao="${x.device_id}" data-val="${x.alerts_only ? 0 : 1}">${x.alerts_only ? "on" : "off"}</button></td></tr>`).join("");
    w.querySelectorAll("[data-consent]").forEach(b => b.onclick = async () => { await api("/api/consent", { device_id: b.dataset.consent, consented: b.dataset.val === "1" }); loadSession(); });
    w.querySelectorAll("[data-ao]").forEach(b => b.onclick = async () => { await api("/api/alerts_only", { device_id: b.dataset.ao, on: b.dataset.val === "1" }); loadSession(); });
    const log = $("#event-log"); log.innerHTML = "";
    s.events.forEach(e => { const r = el("div"); r.appendChild(el("span", "t mono", `t=${e.t}s`)); r.appendChild(el("span", "k", e.kind)); r.appendChild(el("span", "", `${e.device_id || ""} ${e.detail || ""}${e.user ? " · " + e.user : ""}`)); log.appendChild(r); });
    const den = $("#denied-log"); den.innerHTML = s.denied.length ? "" : '<div class="muted">none</div>';
    s.denied.forEach(([t, u, what]) => { const r = el("div"); r.appendChild(el("span", "t mono", `t=${t}s`)); r.appendChild(el("span", "k", u)); r.appendChild(el("span", "", what)); den.appendChild(r); });
    $("#autoplay-btn").textContent = `Scripted scenario: ${s.autoplay ? "on" : "off"}`;
  }

  // ---------- demo controls ----------
  async function loadDemo() {
    const s = await api("/api/session");
    const sel = $("#demo-jersey");
    if (!sel.childElementCount) s.wearers.forEach(w => { const o = el("option", "", `#${w.jersey} ${w.full_name}`); o.value = w.jersey; sel.appendChild(o); });
    $("#autoplay-btn").textContent = `Scripted scenario: ${s.autoplay ? "on" : "off"}`;
    renderDemoLog(s.demo_log); if (window.__status) renderCounters(window.__status);
  }
  document.querySelectorAll("[data-ctl]").forEach(b => b.onclick = async () => {
    const r = await api(`/api/demo/${b.dataset.ctl}`, { jersey: Number($("#demo-jersey").value) });
    if (b.dataset.ctl === "autoplay") loadDemo(); else setTimeout(loadDemo, 300);
  });
  function renderCounters(s) {
    const rej = Object.entries(s.rejections).map(([k, v]) => `<tr><td>rejected: ${k}</td><td class="mono">${v}</td></tr>`).join("");
    $("#counters").innerHTML = `<tr><td>accepted packets</td><td class="mono">${s.accepted}</td></tr>${rej}<tr><td>stored measurements</td><td class="mono">${s.stored_measurements}</td></tr><tr><td>repeated IDs ignored</td><td class="mono">${s.ignored_duplicates}</td></tr><tr><td>storage</td><td>${s.storage_fault ? s.storage_fault : "ok"}</td></tr>`;
  }
  function renderDemoLog(rows) {
    const l = $("#demo-log"); l.innerHTML = "";
    rows.forEach(x => { const r = el("div"); r.appendChild(el("span", "t mono", `t=${x.t}s`)); r.appendChild(el("span", "k", x.jersey ? `#${x.jersey}` : "")); r.appendChild(el("span", "", `${x.action}${x.user ? " · " + x.user : ""}`)); l.appendChild(r); });
  }

  // ---------- athlete view ----------
  async function loadAthlete() { renderAthleteTile(); await renderAthleteChart(); }
  function renderAthleteTile() {
    const t = tiles.get(me.device_id); if (!t) return;
    const n = $("#athlete-tile"); n.innerHTML = ""; n.appendChild(tileNode(t, true));
  }
  async function renderAthleteChart() {
    const d = await api(`/api/wearer/${me.device_id}`);
    $("#athlete-chart").innerHTML = ""; $("#athlete-chart").appendChild(chart(d, 760, 420));
    const c = $("#athlete-consent"); c.innerHTML = "";
    c.appendChild(el("p", "", d.consented ? "Consent: recorded. Your readings are collected for this session and shown only to you and team staff." : "Consent: not recorded. Nothing is collected from your pod."));
    if (d.consented) { const b = el("button", "btn", strings.withdraw); b.onclick = async () => { await api("/api/consent", { device_id: me.device_id, consented: false }); renderAthleteChart(); }; c.appendChild(b); }
  }
  $("#try-peer").onclick = async () => {
    try { await api("/api/wearer/pod-07"); $("#peer-result").textContent = "Unexpected: allowed"; }
    catch (e) { $("#peer-result").textContent = `Hub answered ${e.status}: ${e.message}. This attempt was logged.`; }
  };
  setInterval(() => { if (screen === "athlete") renderAthleteChart(); }, 5000);

  boot();
})();
