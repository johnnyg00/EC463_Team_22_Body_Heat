"""View model rules for the F1 staff and athlete screens."""

import pytest

from f1_sideline_demo.feed import demo_pods, demo_roster
from f1_sideline_demo.hub import Hub
from f1_sideline_demo.views import (AccessDenied, FORBIDDEN_WORDS,
                                    STATE_LABELS, build_alert_strip,
                                    build_roster, diff_tiles,
                                    get_athlete_view, get_staff_roster)


def loaded_hub(t_end=5):
    roster = demo_roster()
    pods = demo_pods()
    hub = Hub()
    for m in roster:
        hub.enroll(m["device_id"], consented=True)
    for t in range(t_end + 1):
        for pod in pods.values():
            p = pod.broadcast(t)
            if p:
                hub.accept(p, t)
    return hub, roster, pods


def test_states_use_color_independent_wellness_wording():
    assert STATE_LABELS, "every state needs a text label"
    for label in STATE_LABELS.values():
        low = label.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in low


def test_primary_state_priority_and_badges():
    hub, roster, _ = loaded_hub()
    dev = "pod-03"
    hub.accept({"device_id": dev, "counter": 100, "measurement_id": "mx",
                "skin_c": 38.0, "air_c": 22.0, "humidity_pct": 45.0,
                "motion": 0.5, "battery_pct": 15.0, "alert": "trend",
                "skin_contact": True}, t=10)
    tile = next(t for t in build_roster(hub, roster, 10) if t["jersey"] == 3)
    assert tile["state"] == "trend"                # alert wins over low battery
    assert tile["label"] == "Rising faster than team setting"
    assert "low battery" in tile["badges"]         # battery still shows as badge


def test_roster_grid_is_fixed_jersey_order():
    hub, roster, _ = loaded_hub()
    tiles = build_roster(hub, roster, 5)
    assert [t["jersey"] for t in tiles] == [3, 7, 11, 18, 24, 30]


def test_roster_scales_to_30_wearers():
    roster = [{"jersey": i, "name": f"P.{i}", "device_id": f"pod-{i:02d}"}
              for i in range(1, 31)]
    hub = Hub()
    for m in roster:
        hub.enroll(m["device_id"], consented=True)
    tiles = build_roster(hub, roster, 0)
    assert len(tiles) == 30
    assert [t["jersey"] for t in tiles] == list(range(1, 31))


def test_only_changed_tiles_are_pushed():
    hub, roster, _ = loaded_hub(t_end=5)
    before = build_roster(hub, roster, 5)
    after = build_roster(hub, roster, 8)  # 3 s later, readings now older
    changed = diff_tiles(before, after)
    assert len(changed) == 6
    # An untouched second pass yields no changes.
    assert diff_tiles(after, after) == []


def test_alert_strip_newest_first_and_ack_moves_lower_not_off():
    hub, roster, _ = loaded_hub()
    for dev, t_alert, counter in (("pod-03", 10, 100), ("pod-07", 20, 100)):
        hub.accept({"device_id": dev, "counter": counter,
                    "measurement_id": f"m-{dev}", "skin_c": 38.0,
                    "air_c": 22.0, "humidity_pct": 45.0, "motion": 0.5,
                    "battery_pct": 90.0, "alert": "trend",
                    "skin_contact": True}, t=t_alert)
    strip = build_alert_strip(hub, roster, 30)
    assert [r["device_id"] for r in strip] == ["pod-07", "pod-03"]  # newest first
    hub.acknowledge("pod-07", user="trainer", t=31)
    strip = build_alert_strip(hub, roster, 31, acknowledged={"pod-07"})
    assert [r["device_id"] for r in strip] == ["pod-03", "pod-07"]  # ack moves lower
    assert len(strip) == 2                                          # still listed


def test_role_checked_on_every_request():
    hub, roster, _ = loaded_hub()
    # Staff sees the full roster.
    assert len(get_staff_roster(hub, roster, 5, role="staff")) == 6
    with pytest.raises(AccessDenied):
        get_staff_roster(hub, roster, 5, role="athlete")
    # Athlete sees only their own tile.
    tile = get_athlete_view(hub, roster, 5, role="athlete",
                            athlete_device_id="pod-11",
                            requested_device_id="pod-11")
    assert tile["jersey"] == 11
    with pytest.raises(AccessDenied):
        get_athlete_view(hub, roster, 5, role="athlete",
                         athlete_device_id="pod-11",
                         requested_device_id="pod-24")
    with pytest.raises(AccessDenied):
        get_athlete_view(hub, roster, 5, role="guest",
                         athlete_device_id=None, requested_device_id="pod-11")


def test_demo_scenario_alert_fires_and_clears():
    from f1_sideline_demo.feed import TREND_LIMIT_C_PER_MIN
    roster = demo_roster()
    pods = demo_pods()
    hub = Hub()
    for m in roster:
        hub.enroll(m["device_id"], consented=True)
    pods["pod-11"].start_scenario_rise()
    fired_at = None
    cleared_at = None
    for t in range(76):
        p = pods["pod-11"].broadcast(t)
        if p:
            hub.accept(p, t)
        alert = hub.state("pod-11").alert
        if fired_at is None and alert == "trend":
            fired_at = t
        if fired_at is not None and alert is None and cleared_at is None:
            cleared_at = t
    assert fired_at is not None, "the scripted rise never crossed the trend setting"
    # Sanity: the rise was about 5 C/min at first, over the 0.5 C/min setting.
    assert 5.0 > TREND_LIMIT_C_PER_MIN
    assert fired_at <= 60
    assert cleared_at is not None, "the plateau never cleared the trend alert"
    assert cleared_at > fired_at
