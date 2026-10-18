"""Subsystem F view models: what the staff and athlete screens show, built
from the hub's validated live state. F never decides anything about a
reading; it only displays what the pod decided and the hub accepted.
Access is checked here on every request, not by hiding screen elements."""

from .hub import NO_SIGNAL_AFTER_S  # noqa: F401  (re-exported for tests)


class AccessDenied(Exception):
    pass


# Wellness wording only: compare with the team's own setting, never call a
# value abnormal and never name a condition.
STATE_LABELS = {
    "absolute": "Above team setting",
    "trend": "Rising faster than team setting",
    "no_signal": "No signal",
    "no_contact": "Check pod contact",
    "ok": "OK",
}
FORBIDDEN_WORDS = ["abnormal", "danger", "heat stroke", "heatstroke",
                   "clinical", "diagnosis", "emergency", "critical"]

# Primary state wins by this order; the rest show as small badges.
_STATE_PRIORITY = ["absolute", "trend", "no_signal", "no_contact", "ok"]


def _primary_state(hub, device_id, t):
    state = hub.state(device_id)
    if state.alert:
        return state.alert
    if hub.no_signal(device_id, t):
        return "no_signal"
    if not state.skin_contact:
        return "no_contact"
    return "ok"


def build_tile(hub, member, t):
    state = hub.state(member["device_id"])
    primary = _primary_state(hub, member["device_id"], t)
    badges = []
    if state.reading and state.reading["battery_pct"] <= 20:
        badges.append("low battery")
    if primary not in ("no_contact",) and not state.skin_contact:
        badges.append("contact")
    if primary not in ("absolute", "trend") and state.alert:
        badges.append("alert")
    age = hub.reading_age_s(member["device_id"], t)
    return {
        "jersey": member["jersey"],
        "name": member["name"],
        "state": primary,
        "label": STATE_LABELS[primary],
        "badges": badges,
        "skin_c": state.reading["skin_c"] if state.reading else None,
        "battery_pct": state.reading["battery_pct"] if state.reading else None,
        "age_s": age,
    }


def build_roster(hub, roster, t):
    """Fixed grid in jersey order."""
    return [build_tile(hub, m, t) for m in sorted(roster, key=lambda m: m["jersey"])]


def diff_tiles(old, new):
    """Only the tiles that changed get pushed to open screens."""
    return [n for o, n in zip(old, new) if o != n]


def build_alert_strip(hub, roster, t, acknowledged=()):
    """Active alerts, newest accepted packet first. An acknowledged alert
    moves below unacknowledged ones but stays until the pod clears it."""
    rows = []
    for m in roster:
        state = hub.state(m["device_id"])
        if state.alert:
            rows.append({
                "device_id": m["device_id"],
                "jersey": m["jersey"],
                "label": STATE_LABELS[state.alert],
                "since_s": t - (state.alert_since if state.alert_since is not None else t),
                "acknowledged": m["device_id"] in acknowledged,
            })
    rows.sort(key=lambda r: (r["acknowledged"], r["since_s"]))  # unacknowledged first, newest raised first
    return rows


def get_staff_roster(hub, roster, t, role):
    if role != "staff":
        raise AccessDenied("staff only")
    return build_roster(hub, roster, t)


def get_athlete_view(hub, roster, t, role, athlete_device_id, requested_device_id):
    """Athletes can only ever see their own data, on every request."""
    if role == "staff":
        member = next(m for m in roster if m["device_id"] == requested_device_id)
        return build_tile(hub, member, t)
    if role == "athlete":
        if requested_device_id != athlete_device_id:
            raise AccessDenied("athletes see only their own session")
        member = next(m for m in roster if m["device_id"] == requested_device_id)
        return build_tile(hub, member, t)
    raise AccessDenied("unknown role")


def render_staff_screen(hub, roster, t, acknowledged=()):
    """Text rendering of the staff screen for the terminal demo."""
    strip = build_alert_strip(hub, roster, t, acknowledged)
    lines = [f"BODY HEAT sideline demo - staff view - t={t:3d}s", "-" * 64]
    if strip:
        lines.append("ALERTS (newest first):")
        for r in strip:
            mark = " (acknowledged)" if r["acknowledged"] else ""
            lines.append(f"  #{r['jersey']:>2} {r['label']}{mark}")
    else:
        lines.append("ALERTS: none")
    lines.append("-" * 64)
    for tile in build_roster(hub, roster, t):
        skin = f"{tile['skin_c']:5.2f} C" if tile["skin_c"] is not None else "  --   "
        bat = f"{tile['battery_pct']:5.1f}%" if tile["battery_pct"] is not None else "  -- "
        age = "--" if tile["age_s"] is None else f"{tile['age_s']}s"
        badges = f" [{', '.join(tile['badges'])}]" if tile["badges"] else ""
        lines.append(f"  #{tile['jersey']:>2} {tile['name']:<10} {skin}  {bat}  age {age:>3}  {tile['label']}{badges}")
    return "\n".join(lines)
