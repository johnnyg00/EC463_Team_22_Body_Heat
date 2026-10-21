"""Subsystem F view models for the demo prototype: Table F2 state priority
with the production labels, badges for secondary states, alert strip order,
tile diffs, and the exportable user-facing string table for the wording
audit (AC-F4). F never decides anything about a reading."""

from f1_sideline_demo.hub import NO_SIGNAL_AFTER_S
from f1_sideline_demo.views import FORBIDDEN_WORDS as _F1_FORBIDDEN, diff_tiles  # noqa: F401

# Table F2, priority order, labels verbatim. Each state has a glyph so it
# reads without color (grayscale, sun glare, color-vision deficiency).
STATES = [
    ("absolute",       "Above team temperature setting", "▲"),
    ("trend",          "Rising faster than team setting", "↗"),
    ("no_signal",      f"No signal for {NO_SIGNAL_AFTER_S} s", "⊘"),
    ("detached",       "Pod not on skin",                 "◌"),
    ("low_battery",    "Replace batteries",               "▭"),
    ("ok",             "OK",                              "✓"),
    ("not_collecting", "Not collecting: no consent",      "—"),
]
STATE_LABELS = {k: label for k, label, _ in STATES}
STATE_ICONS = {k: icon for k, _, icon in STATES}

BADGE_LABELS = {
    "low_battery": "Replace batteries",
    "detached": "Pod not on skin",
    "no_signal": "No signal",
    "alert": "Alert active",
    "alerts_only": "Alerts only",
    "resume": "Back below team setting",
    "acknowledged": "Acknowledged",
}

UI_STRINGS = {
    "app_title": "Body Heat",
    "subtitle": "Sideline wellness view",
    "sign_in": "Sign in",
    "sign_out": "Sign out",
    "roster": "Roster",
    "session": "Session",
    "my_session": "My session",
    "alerts_heading": "Alerts",
    "no_alerts": "No active alerts",
    "ack": "Acknowledge",
    "ack_note": "Acknowledging records your name and the time. The alert stays until the pod clears it.",
    "resume_notice": "Back below team setting",
    "connection_lost": "Connection lost - values shown are not live",
    "storage_fault": "Storage fault",
    "consent_withdrawn_prompt": "Consent withdrawn - please stop the wearable",
    "export": "Export CSV",
    "withdraw": "Withdraw consent",
    "intended_use": "Body Heat is a general wellness product. It compares skin temperature with your team's own settings to help athletes and staff stay comfortable in the heat.",
    "reading_age": "updated {age} s ago",
    "stale_sensor": "sensor sample {age} s old",
}

FORBIDDEN_WORDS = sorted(set(_F1_FORBIDDEN) | {
    "heat exhaustion", "fever", "seek medical", "medical", "hyperthermia", "illness",
    "symptom", "risk", "warning", "unsafe", "dangerous", "treat", "patient",
})


def all_user_facing_strings():
    """Everything a person can read on screen, for the AC-F4 audit."""
    return list(STATE_LABELS.values()) + list(BADGE_LABELS.values()) + list(UI_STRINGS.values())


def primary_state(hub, device_id, t):
    state = hub.state(device_id)
    if not hub.is_consented(device_id):
        return "not_collecting"
    if state.alert == "absolute":
        return "absolute"
    if state.alert == "trend":
        return "trend"
    if hub.no_signal(device_id, t):
        return "no_signal"
    if not state.skin_contact:
        return "detached"
    if hub.low_battery.get(device_id):
        return "low_battery"
    return "ok"


def trend_arrow(slope_c_per_5min):
    """Display only, from the slope the pod reported."""
    if slope_c_per_5min is None:
        return ""
    if slope_c_per_5min >= 0.3:
        return "↑"
    if slope_c_per_5min <= -0.3:
        return "↓"
    return "→"


def build_tile(hub, member, t):
    dev = member["device_id"]
    state = hub.state(dev)
    primary = primary_state(hub, dev, t)
    badges = []
    if primary != "not_collecting":
        if hub.no_signal(dev, t) and primary != "no_signal" and state.last_accepted_at is not None:
            badges.append("no_signal")
        if not state.skin_contact and primary != "detached":
            badges.append("detached")
    if hub.low_battery.get(dev) and primary != "low_battery" and primary != "not_collecting":
        badges.append("low_battery")
    if dev in hub.acked and state.alert:
        badges.append("acknowledged")
    if hub.resume_notice(dev, t):
        badges.append("resume")
    if dev in hub.alerts_only:
        badges.append("alerts_only")
    hide_temp = dev in hub.alerts_only or primary == "not_collecting"
    age = hub.reading_age_s(dev, t)
    m_age = hub.measurement_age_s(dev, t)
    reading = state.reading or {}
    return {
        "device_id": dev,
        "jersey": member["jersey"],
        "name": member["name"],
        "state": primary,
        "label": STATE_LABELS[primary],
        "icon": STATE_ICONS[primary],
        "badges": badges,
        "badge_labels": [BADGE_LABELS[b] for b in badges],
        "skin_c": None if hide_temp else reading.get("skin_c"),
        "arrow": "" if hide_temp else trend_arrow(getattr(state, "slope", None)),
        "battery_pct": reading.get("battery_pct"),
        "age_s": age,
        "measurement_age_s": m_age,
        "greyed": primary in ("no_signal", "not_collecting"),
        "alerts_only": dev in hub.alerts_only,
    }


def build_roster(hub, roster, t):
    """Fixed grid in jersey order; tiles never move (AC-F1)."""
    return [build_tile(hub, m, t) for m in sorted(roster, key=lambda m: m["jersey"])]


def build_alert_strip(hub, roster, t):
    """Active alerts, newest first; acknowledged alerts demoted below
    unacknowledged ones but listed until the pod clears them (AC-F7)."""
    rows = []
    for m in roster:
        dev = m["device_id"]
        state = hub.state(dev)
        if state.alert and hub.is_consented(dev):
            ack = hub.acked.get(dev)
            rows.append({
                "device_id": dev, "jersey": m["jersey"], "name": m["name"],
                "state": state.alert, "label": STATE_LABELS[state.alert], "icon": STATE_ICONS[state.alert],
                "since_s": t - (state.alert_since if state.alert_since is not None else t),
                "acknowledged": ack is not None,
                "ack_by": ack[1] if ack else None, "ack_t": ack[0] if ack else None,
            })
    rows.sort(key=lambda r: (r["acknowledged"], r["since_s"]))
    return rows


def build_notices(hub, roster, t):
    """Resume notices (FR-009) and stop-the-wearable prompts (AC-F6)."""
    out = []
    for m in roster:
        dev = m["device_id"]
        if hub.resume_notice(dev, t):
            out.append({"kind": "resume", "jersey": m["jersey"], "name": m["name"], "text": UI_STRINGS["resume_notice"]})
        if not hub.is_consented(dev) and hub.state(dev).last_accepted_at is not None:
            out.append({"kind": "stop_wearable", "jersey": m["jersey"], "name": m["name"], "text": UI_STRINGS["consent_withdrawn_prompt"]})
    return out


def build_status(hub, t, storage):
    return {
        "t": t,
        "accepted": hub.accepted,
        "rejections": dict(hub.rejections),
        "stored_measurements": storage.measurement_count(),
        "ignored_duplicates": storage.ignored_duplicates,
        "storage_fault": hub.storage_fault,
    }
