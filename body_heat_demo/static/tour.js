/* Guided tour: a spotlight walks the viewer through the live screen, makes real
   things happen (an alert, a tampered packet) and explains what they see.
   Uses window.BH from app.js. Keyboard: → / Enter next, ← back, Esc leave. */
(() => {
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, ms = 8000) => { const end = Date.now() + ms; while (Date.now() < end) { const v = fn(); if (v) return v; await sleep(200); } return null; };

  let steps = [], i = 0, active = false, ui = null, resizeHandler = null;

  // ---------- step definitions ----------
  const TOUR_JERSEY = 12;          // #12 J. Costa is not used by the scripted scenario
  const tileSel = (j) => `.tile[data-dev="pod-${String(j).padStart(2, "0")}"]`;

  function staffSteps(BH) {
    return [
      { title: "Welcome to Body Heat", body: "This is the sideline screen a coach, athletic trainer or crew supervisor keeps open. In about two minutes we'll make a real alert happen and show how the hub handles it. Nothing here is medical: the pods compare skin temperature with the limits your own team chose.", center: true, cta: "Start the tour" },
      { title: "One glance", target: "#summary", body: "The top line answers the only question that matters at a glance: is anyone outside the team's settings? Green means everyone is fine. Pills count how many people are in each state.", before: () => BH.show("roster") },
      { title: "One card per person, and they never move", target: "#grid", body: "Thirty people fit on one laptop screen without scrolling. Cards stay in jersey order, so you can find the same person in the same place every time you look. They are never re-sorted by severity." },
      { title: "How to read a card", target: tileSel(2), place: "right", body: "Name, skin temperature to 0.1 °C, a trend arrow, a status in words with a symbol, and how fresh the reading is. The colour, the symbol and the words always agree, so it reads in sun glare or in black and white." },
      { title: "Who decides?", target: tileSel(2), place: "right", body: "Not this screen. Each pod on the arm decides its own alert, from a rate-of-rise rule and an absolute limit, and buzzes the wearer. The hub checks each packet and the screen only shows what the pod decided. That keeps the alert working even if this laptop is slow." },
      { title: "Let's make something happen", target: tileSel(TOUR_JERSEY), place: "left", body: `Watch #${TOUR_JERSEY}. We'll have their pod read above the team's temperature limit. Within a second the pod raises the alert and this card changes.`, cta: "Raise an alert", action: async () => { await BH.api(`/api/demo/spike`, { jersey: TOUR_JERSEY }); await waitFor(() => $(tileSel(TOUR_JERSEY))?.classList.contains("absolute"), 6000); } },
      { title: "The card changed", target: tileSel(TOUR_JERSEY), place: "left", body: "Red, striped, with a triangle and the words \"Above team temperature setting\". The same alert appears in the attention panel above and in the summary line. All three happened within a second of the pod's decision." },
      { title: "The attention panel", target: ".alert-row", place: "below", body: "Newest alerts first, each with a plain next step: what to do, not a diagnosis. Press \"Got it\" to record that you have seen it. It records your name and the time.", before: async () => { await waitFor(() => $(".alert-row")); } },
      { title: "\"Got it\" is a note, not a dismissal", target: ".alert-row", place: "below", body: "The row moved below any new alerts and is dimmed, but it stays listed. Only the pod can clear its own alert, when it sees the wearer back within the settings. No one can silence an alert from this screen.", cta: "Press Got it for me", action: async () => { await BH.api("/api/ack", { device_id: `pod-${TOUR_JERSEY}` }); await waitFor(() => $(".alert-row.acked"), 5000); } },
      { title: "Open a person", target: tileSel(TOUR_JERSEY), place: "left", body: "Tap any card for the full story: the session chart with skin and air temperature, humidity and movement, markers where alerts were raised, cleared and noted, and the same \"what to do\" line.", cta: "Open #12", action: async () => { await BH.openDetail(`pod-${TOUR_JERSEY}`); await waitFor(() => $("#detail-chart svg")); } },
      { title: "The session chart", target: "#detail-chart", place: "left", body: "Every reading the hub accepted today. The dashed red line is the team's limit. The red marker on the right is the alert we just raised; the dotted marker is your \"Got it\". Nothing is ever deleted." },
      { title: "Per-person switches", target: "#detail-actions", place: "right", body: "Two things staff control here. \"Alerts only\" hides the temperature number on the live screen for someone who would rather not share it, while still showing alerts. The consent switch stops the hub recording that pod within seconds.", after: () => BH.closeDetail() },
      { title: "Setup: consent first", target: "#wearers", place: "left", body: "Consent is opt-in and the hub enforces it when packets arrive, not just on screen. Switch someone off and their packets are rejected and counted, their card says \"Not collecting\", and staff are prompted to switch the pod off. #33 never opted in, which is why their card is grey.", before: async () => { BH.show("session"); await waitFor(() => $("#wearers tr:nth-child(3)")); } },
      { title: "Team settings and export", target: "#screen-session .fixed", place: "right", body: "The rate-of-rise and temperature limits are chosen here at setup and sent to the pods. One click exports the session as a spreadsheet, with wearer IDs and timestamps, and people who did not opt in left out." },
      { title: "The hub checks every packet", target: "#counters", place: "left", body: "Packets arrive once a second from each pod, encrypted, carrying only a device ID. The hub rejects anything unknown, tampered, replayed or without consent, and counts it. Let's send a tampered packet and watch the counter move.", before: async () => { BH.show("demo"); await waitFor(() => $("#counters tr")); }, cta: "Send a tampered packet", action: async () => { const before = Number($("#counters tr:nth-child(4) td:last-child")?.textContent || 0); await BH.api("/api/demo/altered", { jersey: 2 }); await waitFor(() => Number($("#counters tr:nth-child(4) td:last-child")?.textContent || 0) > before, 5000); } },
      { title: "Rejected and counted", target: "#counters", place: "left", body: "\"Rejected: altered\" went up by one. Nothing on the live screen changed, because a rejected packet never updates a reading or refreshes a pod's signal status. The same happens for replayed packets and unknown pods." },
      { title: "Presenter tools", target: ".ctl-grid", place: "right", body: "For demonstrations only: these buttons stand in for real pods. Make someone's temperature climb, take a pod off skin, run the batteries down, or pull the disk. The scripted scenario on the right also runs by itself." },
      { title: "Help is one tap away", target: "#guide-btn", place: "below", body: "The Guide lists every card state with what it means and what to do. The Display button flips between the bright sunlight look and a dark look for evenings.", before: () => BH.show("roster") },
      { title: "That's the tour", center: true, body: `We'll put #${TOUR_JERSEY}'s pod back to normal now; watch the pod clear its own alert and the card show "Back below team setting". Explore freely, replay the tour any time from the ▶ Tour button, or sign in as athlete11 to see what a wearer sees.`, cta: "Finish", action: async () => { await BH.api("/api/demo/unspike", { jersey: TOUR_JERSEY }); } },
    ];
  }

  function athleteSteps(BH) {
    return [
      { title: "Your own view", center: true, body: "As a wearer you see only your own session: your status, your chart and your consent. Staff see the team; nobody else can see you.", cta: "Start" },
      { title: "Your status right now", target: "#athlete-status", place: "below", body: "A headline in plain words, what it means for you, and your latest reading. Alerts come from the pod on your arm, which also buzzes, so you will know before anyone else does." },
      { title: "Your day", target: "#athlete-chart", place: "right", body: "Skin and air temperature, humidity and movement since the session began, with markers where your pod raised or cleared an alert." },
      { title: "Your consent, your choice", target: "#athlete-consent", place: "left", body: "Nothing is recorded until you opt in. Withdraw at any time: within seconds the hub stops accepting your pod's packets and staff are asked to switch it off." },
      { title: "The hub checks every request", target: "#try-peer", place: "left", body: "Try asking for a teammate's data. The hub refuses and writes the attempt in the log. This is enforced on the hub, not by hiding buttons.", cta: "Try it", action: async () => { $("#try-peer").click(); await sleep(800); } },
      { title: "That's it", center: true, body: "Replay any time from the ▶ Tour button.", cta: "Finish" },
    ];
  }

  // ---------- rendering ----------
  function build() {
    ui = el("div", "tour");
    ui.innerHTML = `<div class="tour-spot"></div><div class="tour-card" role="dialog" aria-live="polite">
      <div class="tour-progress"><span class="tour-count"></span><span class="tour-dots"></span></div>
      <h3 class="tour-title"></h3><p class="tour-body"></p>
      <div class="tour-btns"><button class="btn ghost tour-skip">Leave tour</button><span class="grow"></span><button class="btn tour-back">← Back</button><button class="btn primary tour-next">Next →</button></div></div>`;
    document.body.appendChild(ui);
    ui.querySelector(".tour-skip").onclick = end;
    ui.querySelector(".tour-back").onclick = () => go(i - 1);
    ui.querySelector(".tour-next").onclick = next;
    resizeHandler = () => position(steps[i]);
    addEventListener("resize", resizeHandler);
    document.addEventListener("keydown", keys);
  }
  function keys(e) {
    if (!active) return;
    if (e.key === "Escape") end();
    else if (e.key === "ArrowRight" || e.key === "Enter") { e.preventDefault(); next(); }
    else if (e.key === "ArrowLeft") go(i - 1);
  }
  function position(step) {
    const spot = ui.querySelector(".tour-spot"), card = ui.querySelector(".tour-card");
    const target = step.center ? null : document.querySelector(step.target);
    if (!target) {   // centred step: keep the dim backdrop, no spotlight ring
      spot.classList.add("bare");
      Object.assign(spot.style, { opacity: "1", left: "50%", top: "50%", width: "0", height: "0" });
      card.className = "tour-card center"; card.style.left = card.style.top = ""; return;
    }
    spot.classList.remove("bare");
    const r = target.getBoundingClientRect(), pad = 8;
    Object.assign(spot.style, { opacity: "1", left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + 2 * pad}px`, height: `${r.height + 2 * pad}px` });
    card.className = "tour-card";
    const cw = 420, ch = card.offsetHeight || 220, gap = 16, vw = innerWidth, vh = innerHeight;
    let place = step.place || "below", left, top;
    if (place === "below" && r.bottom + gap + ch > vh) place = "above";
    if (place === "above" && r.top - gap - ch < 0) place = "below";
    if (place === "right" && r.right + gap + cw > vw) place = "left";
    if (place === "left" && r.left - gap - cw < 0) place = "below";
    if (place === "below") { left = r.left + r.width / 2 - cw / 2; top = r.bottom + gap; }
    else if (place === "above") { left = r.left + r.width / 2 - cw / 2; top = r.top - gap - ch; }
    else if (place === "right") { left = r.right + gap; top = r.top + r.height / 2 - ch / 2; }
    else { left = r.left - gap - cw; top = r.top + r.height / 2 - ch / 2; }
    left = Math.max(12, Math.min(vw - cw - 12, left)); top = Math.max(12, Math.min(vh - ch - 12, top));
    card.style.left = `${left}px`; card.style.top = `${top}px`;
    target.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  async function go(n) {
    if (n < 0 || n >= steps.length) return;
    if (ui.querySelector(".tour-card").classList.contains("busy")) return;
    const prev = steps[i];
    if (prev && prev.after && n > i) await prev.after();
    i = n;
    const step = steps[i];
    const card = ui.querySelector(".tour-card");
    card.classList.add("busy");
    if (step.before) await step.before();
    await sleep(150);
    ui.querySelector(".tour-count").textContent = `${i + 1} of ${steps.length}`;
    ui.querySelector(".tour-dots").innerHTML = steps.map((_, k) => `<i class="${k === i ? "on" : k < i ? "done" : ""}"></i>`).join("");
    ui.querySelector(".tour-title").textContent = step.title;
    ui.querySelector(".tour-body").textContent = step.body;
    const nextBtn = ui.querySelector(".tour-next");
    nextBtn.textContent = step.cta ? `${step.cta} →` : (i === steps.length - 1 ? "Finish" : "Next →");
    ui.querySelector(".tour-back").disabled = i === 0;
    card.classList.remove("busy");
    position(step);
    requestAnimationFrame(() => position(step));
    nextBtn.focus();
  }
  async function next() {
    const step = steps[i];
    const nextBtn = ui.querySelector(".tour-next");
    if (!step || nextBtn.disabled || ui.querySelector(".tour-card").classList.contains("busy")) return;
    if (step.action) {
      nextBtn.disabled = true; nextBtn.textContent = "Working…";
      try { await step.action(); } catch (e) { console.warn(e); }
      nextBtn.disabled = false;
    }
    if (i === steps.length - 1) end(); else go(i + 1);
  }
  function start(BH, role) {
    if (active) return;
    active = true;
    steps = role === "staff" ? staffSteps(BH) : athleteSteps(BH);
    i = -1;
    build();
    go(0);
  }
  function end() {
    if (!active) return;
    active = false;
    removeEventListener("resize", resizeHandler); document.removeEventListener("keydown", keys);
    ui.remove(); ui = null;
    try { localStorage.setItem("bh-toured", "1"); } catch (e) {}
    if (window.BH) window.BH.toast("Tour ended. Replay it any time with ▶ Tour.");
  }

  window.BHTour = { start, end, get active() { return active; } };
})();
