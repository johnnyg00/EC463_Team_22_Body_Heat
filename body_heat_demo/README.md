# Body Heat demo prototype (hub + views, synthetic data)

Team 22 Body Heat, EC463 Fall 2026. A hard-coded, presentation-ready
prototype of the software platform: Subsystem E (hub receiver, validation,
live state, store, consent, export) and Subsystem F (supervisor roster,
alert strip, athlete detail, session/consent/export, athlete view), served as
a local web app with live updates over Server-Sent Events.

**All wearer data is synthetic.** Thirty fake wearers (six "pods" plus 24
emulated sources, per SAC-03) broadcast once per second from a scripted
scenario; nothing here talks to real hardware. It builds on the F1 sideline
demo (`f1_sideline_demo/`, issue #81) and reuses its hub and view rules.

Stakeholder: the athletic trainer monitoring a team from one sideline
screen (PRD G-02). Use case for the demo: outdoor team practice in late
summer heat. Stakeholder input came from the team's athlete interview.

## Run

    python3 -m body_heat_demo.server            # http://localhost:8463
    python3 -m body_heat_demo.server --no-autoplay   # presenter triggers everything

Standard library only, Python 3.9+. No internet, no CDN, no cloud.

Sign in (password `demo` for every account):

| account | role | sees |
|---|---|---|
| `k.smith` | staff (athletic trainer) | roster, session, demo controls |
| `z.elkilany` | staff | same |
| `athlete11`, `athlete07`, ... `athlete33` | athlete | own session only |

## Test

    python3 -m pytest tests/ -v       # F1 tests + demo prototype tests

## Guided tour

After signing in, a welcome card offers a two-minute guided tour (also the
**▶ Tour** button in the header, any time). It spotlights each part of the
screen and makes real things happen as it goes: it raises an alert on #12
(the pod reads above the limit and decides for itself), records "Got it",
opens the chart, visits Setup, sends a tampered packet and shows the hub
reject and count it, then puts #12 back and lets the pod clear its own
alert. Keyboard: → or Enter next, ← back, Esc leave. Wearer accounts get a
shorter tour of their own view. Code: `static/tour.js`.

## What the presentation shows

Scripted timeline (also triggerable from **Demo controls**):

| t (s) | event | what the screen shows |
|---|---|---|
| 5 | #18 battery low | "Replace batteries" tile |
| 10 | #24 pod stops broadcasting | "No signal for 60 s" at t=70, last values greyed |
| 15 | #11 skin temperature climbs | pod raises **trend** alert (~t=60), then **absolute** (~t=90); alert strip, acknowledge button |
| 40–110 | #7 pod off skin | "Pod not on skin"; pod suspends its own temperature decisions |
| 150–154 | replayed, altered and unknown-device packets | rejected and counted on the Hub counters panel; nothing on the roster changes |
| 170 | #11 eases back | pod clears the alert; "Back below team setting" resume notice |
| always | #33 never opted in | "Not collecting: no consent"; excluded from export |
| always | #3 alerts-only | temperature hidden, alert state still shown |

Also: click any tile for the session chart (skin, air, humidity, motion, alert
and acknowledgement markers); **Session** for consent, alerts-only, team
settings, CSV export, session log and denied-request log; sign in as an
athlete to see the own-data-only view and try the "request a teammate's
data" button (denied by the backend and logged, AC-F5). Pull the laptop's
network or stop the server to see the "Connection lost" banner (AC-F9).

## Rules this prototype keeps (and where)

| rule | where |
|---|---|
| Alert decisions on the pod only; F displays | `engine.SyntheticPod._decide_alert`, `views.py` has no thresholds |
| Ingest order: format → enrollment → auth → consent → counter; live state before storage | `hub.DemoHub.accept` |
| Rejected packets do not refresh readings or comm status; counted | `hub.rejections` |
| Repeated measurement IDs never add rows (AC-E5) | `store.measurements` primary key |
| No-signal 60 s after last accepted packet (AC-E3) | `f1_sideline_demo.hub.NO_SIGNAL_AFTER_S` |
| Acknowledgement recorded with time + user, never clears (AC-F7) | `hub.acknowledge`, `views.build_alert_strip` |
| Consent withdrawal stops ingest, prompts staff to stop the wearable (AC-F6) | `hub.set_consent`, `views.build_notices` |
| Table F2 priority with verbatim labels, glyph + stripe pattern per state (AC-F3) | `views.STATES`, `static/app.css` |
| Wording audit: every user-facing string vs banned list (AC-F4) | `views.all_user_facing_strings`, `tests/test_demo_prototype.py` |
| RBAC in the backend on every request, denials logged (AC-F5) | `auth.Accounts.require_*`, `server.py` |
| Fixed 6×5 grid in jersey order, 30 tiles at 1366×768 (AC-F1) | `static/app.css .grid` |
| SSE pushes only changed tiles | `server.Handler._sse`, `views.diff_tiles` |
| Storage failure is a visible fault; reception continues | `hub._drain`, banner in `app.js` |
| Export excludes non-consenting wearers, includes consent records | `store.export_csv` |

## Demo shortcuts (not the December design)

- Trend window is 60 s so an alert appears on stage; the pod design uses a
  5-min / 31-reading least-squares window.
- "Authentication" checks a synthetic `tag` field; the real AES-128 AEAD
  construction, byte format and key provisioning are **[OPEN]** (S1 sheet).
- Store is plain in-memory `sqlite3`; the hub uses SQLCipher (TM-23).
- Passwords are salted scrypt hashes (salted PBKDF2-SHA512 on Python builds
  without `hashlib.scrypt`, such as macOS's bundled `python3`); the hub uses
  Argon2id (6.4).
- Synthetic pods call `hub.accept()` directly. One slot can instead be fed by
  the real TempMonitor pod over BLE (`--ble-pod`, below), through the same call.
- A non-consenting wearer shows "Not collecting" instead of "No signal",
  even though Table F2 ranks no-signal higher. Nothing is collected from
  them, so no-signal would be misleading. Confirm with Krista (F owner).

## Real pod over Bluetooth (TempMonitor)

Mathis's firmware package `TempMonitor_v1.1.0_USB_Power_Fix/` (component-testing
branch: XIAO ESP32S3 + TMP117 + SHT45, on-pod alert rules, BLE) can feed one
roster slot. The other 29 wearers stay synthetic.

```sh
python3.12 -m pip install -r TempMonitor_v1.1.0_USB_Power_Fix/requirements.txt   # bleak, Python 3.10+
python3.12 TempMonitor_v1.1.0_USB_Power_Fix/receiver.py --list                    # optional: board IDs
python3.12 -m body_heat_demo.server --ble-pod 2 [--ble-device ID]
```

Close the standalone `receiver.py` first: the board takes one central at a
time. On macOS, start it from a terminal app that has Bluetooth permission
(System Settings → Privacy & Security → Bluetooth). macOS kills a process that
uses Bluetooth without permission, so the server probes in a child process
first and stops with an explanation instead of crashing. Use a jersey with no scripted or tour events (2 is free; avoid 3, 7, 11,
12, 18, 24). The card shows a **BLE** tag, and Presenter tools → Hub counters
shows the link, reading and decision status, and the pod's own threshold.

`body_heat_demo/pod_bridge.py` imports the package's `receiver.py` unchanged
(decoders, freshness watches, board selection). It maps each 1 s sensor
notification onto the existing hub packet, carrying the latest 10 s alert
decision, and calls `Engine.ingest_external` → `DemoHub.accept`. Consent,
counter, live state, analysis and storage are therefore the same path.

What differs from the synthetic pods (see the module docstring for detail):

- **Not authenticated.** The bench firmware has no pairing or encryption. The
  bridge stamps the hub's synthetic session/tag as the local collector.
- **Pod decision unavailable** (new state, not in Table F2 yet; needs F-owner
  sign-off). It shows when the pod has no current decision: startup, sensor
  fault, no alert event for 15 s, a dropped link, or sensor-only firmware. It
  is never OK. An active alert is held with this badge and is cleared only by
  a valid pod decision, so a fault never produces "Back below team setting".
- **Rising check not ready yet** (badge on OK). The pod's trend window takes 5
  min (31 samples) to fill after every boot or history restart. Until then, OK
  covers the temperature limit only. Warm-up is not a "not rising" result.
- **No battery gauge, IMU or contact sensor** on this board. Battery and
  movement are unknown (not stored, never zero). "Pod not on skin" cannot show.
- **TMP117 WAIT/error** shows "No reading", stores nothing for skin, and skips
  hub analysis for that packet (a sensor gap, not an analysis failure).
- **Thresholds differ.** The pod alerts above 29.0 °C (Mathis's bench demo
  setting) and at 0.06 °C/min over 5 min. The hub analyzer and Setup page use
  the team settings (38.5 °C, 1.0 °C per 5 min).
- **Freshness.** A new pod decision reaches the hub as soon as it arrives, even
  if readings are late. It rides on the last reading under the same measurement
  ID, so nothing new is stored and the reading's age keeps growing. If the
  decision is more than 15 s old, the hub marks it unavailable within about a
  second, even while the link stays up. If no
  reading arrives for 15 s, the bridge reconnects to the same board. On
  connect, the stored alert value is read before subscribing. It only names a
  fault the firmware will not resend; it is never shown as a current decision.
- **LLM analyzers and cost.** This pod sends a new reading every second, not
  every 10 s, so its 5-min analysis window holds about 300 readings instead of
  30. With `--real-provider`, expect about 10× the prompt tokens for this
  wearer. The offline fake client is unaffected.

Offline tests use the protocol's golden packets and a fake radio
(`tests/test_pod_bridge.py`). They do not replace a live run with the board.

## Integrated live hub analysis

Run from the repository root:

```sh
python3 -m body_heat_demo.server --analyzer deterministic --port 8463
# Also available: --analyzer relay | llm-explain | llm-rolling
# Optional presenter-only start: --no-autoplay
```

Every hub-accepted packet is passed to the selected `analysis_scaffold` analyzer
with the same session second, including rebroadcasts. Rejected packets never
reach analysis; the scaffold handles its own measurement deduplication. Seeded
chart history is not live input. The default `deterministic` choice is local,
zero-cost and requires no key. `relay` runs the pod comparator and preserves the
prior view payload/display. `llm-explain` uses deterministic flags with model
explanations; `llm-rolling` scores rolling windows. Both use `FakeLLMClient`
offline by default, even if a key is present. Real calls require the explicit
`--real-provider` flag with an LLM choice and `OPENROUTER_API_KEY` in the environment;
this reuses the scaffold's existing bounded provider path.

Staff roster cards show **one dashed Hub line** under the pod status, so the
fixed 6 × 5 grid keeps all 30 people on one screen: `Hub flag: above limit · 1:30`
(latest flag and session time), else the latest coverage notice, else
`Hub: no flags yet` once the analyzer has processed readings for that wearer
(`Hub: waiting for readings` before that). Staff detail cards show the full
**Hub analysis** section beside the pod status: the latest flag kind and the
analyzer's exact reviewed C-05 wording, including `resume`. This is a latest-event
display, not a new OK decision or a replacement for pod alerts. Before a flag it
says “No flags yet” (or “Awaiting analysis output” before any reading reached the
analyzer). Analysis notices include their event time and
remain visible as the latest coverage notice: `analysis unavailable`,
`budget_exhausted`, or `model_deferred`. Session-wide budget exhaustion is shown
for every consenting wearer. A notice never becomes an OK result. No analysis
fields are sent in athlete detail JSON or athlete SSE. Non-consenting wearers
have no analysis section. Consent withdrawal stops future analyzer ingestion.

Standing labels: **implementation assumptions by the coding agent**;
**bounded-call offline-verified**; **real model untested**. Thresholds are
**team demo settings, not validated health thresholds**. Fake outputs verify
software wiring only, not model effectiveness, real token usage, latency or cost.
The live app retains the scaffold's cadence, recovery windows and reviewed
wording unchanged. Call budgets use the scaffold's formula sized for
`--session-minutes` (default 60) instead of the 200 s replay scenario, which ran
out about 4 minutes into a live session; real-provider attempts stay capped by
the fixed `LIVE_SAFETY_CEILING` (300 for the server process). Real calls are synchronous in the engine ingest
path and can delay demo ticks until the existing provider timeout; offline mode
is the verified presentation path.

Offline checks:

```sh
python3 -m pytest tests/ analysis_scaffold/tests/ -q
python3 -m analysis_scaffold.record
python3 -m analysis_scaffold.replay analysis_scaffold/recordings/session_default.jsonl --analyzer all
```
