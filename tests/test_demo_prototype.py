"""Tests for the demo prototype (body_heat_demo): ingest order and rejection
counters (AC-E4/E5/E6), no-signal window (AC-E3), ack never clears (AC-F7),
Table F2 priority (AC-F3), wording audit (AC-F4), cross-athlete access denied
and logged (AC-F5), consent withdrawal stops data (AC-F6), export excludes
non-consenting wearers (AC-E8), storage fault does not stop reception, and a
scaled AC-E7 load run (30 sources)."""

import time

import pytest

from body_heat_demo import views
from body_heat_demo.auth import AccessDenied, Accounts
from body_heat_demo.engine import SESSION_ID, SyntheticPod, build_demo
from body_heat_demo.hub import DemoHub
from body_heat_demo.store import Store
from f1_sideline_demo.hub import NO_SIGNAL_AFTER_S, Rejected


def packet(dev="pod-02", counter=1, mid="m1", **over):
    p = {"device_id": dev, "session_id": SESSION_ID, "counter": counter, "measurement_id": mid,
         "skin_c": 36.0, "air_c": 30.0, "humidity_pct": 55.0, "motion": 0.4, "battery_pct": 90.0,
         "alert": None, "skin_contact": True, "tag": "ok"}
    p.update(over)
    return p


@pytest.fixture
def hub():
    h = DemoHub(Store(), SESSION_ID)
    h.enroll("pod-02", consented=True)
    h.enroll("pod-03", consented=False)
    return h


# -- E: ingest --------------------------------------------------------------------
def test_ingest_order_and_counters(hub):
    with pytest.raises(Rejected):
        hub.accept({"device_id": "pod-02"}, 0)                       # malformed
    with pytest.raises(Rejected):
        hub.accept(packet(dev="pod-99"), 0)                           # unknown
    with pytest.raises(Rejected):
        hub.accept(packet(tag="bad"), 0)                              # altered
    with pytest.raises(Rejected):
        hub.accept(packet(dev="pod-03"), 0)                           # no consent
    assert hub.accept(packet(counter=5), 0)
    with pytest.raises(Rejected):
        hub.accept(packet(counter=5), 1)                              # replay
    assert hub.rejections == {"malformed": 1, "unknown": 1, "altered": 1, "no_consent": 1, "replayed": 1}
    assert hub.accepted == 1
    assert hub.state("pod-02").last_accepted_at == 0                  # rejects never refresh comm status


def test_repeated_measurement_id_does_not_duplicate_rows(hub):
    for c in range(1, 11):
        hub.accept(packet(counter=c, mid="m-10"), c)
    hub.accept(packet(counter=11, mid="m-20", alert="trend"), 11)
    hub.flush()
    assert hub.store.measurement_count() == 2 * 4                     # two sample sets x four signals
    assert hub.state("pod-02").alert == "trend"                       # new alert still flowed (AC-E5)


def test_no_signal_window(hub):
    hub.accept(packet(), 0)
    assert not hub.no_signal("pod-02", NO_SIGNAL_AFTER_S - 1)
    assert hub.no_signal("pod-02", NO_SIGNAL_AFTER_S)
    hub.tick(NO_SIGNAL_AFTER_S)
    hub.flush()
    assert any(e["kind"] == "no_signal" for e in hub.store.events())


def test_ack_never_clears_alert(hub):
    hub.accept(packet(counter=1, alert="absolute"), 0)
    hub.acknowledge("pod-02", "k.smith", 5)
    assert hub.state("pod-02").alert == "absolute"
    assert hub.acked["pod-02"] == (5, "k.smith")
    hub.accept(packet(counter=2, mid="m2", alert=None), 6)            # only the pod clears it
    assert hub.state("pod-02").alert is None and "pod-02" not in hub.acked
    assert hub.resume_notice("pod-02", 30)


def test_consent_withdrawal_stops_data(hub):
    hub.accept(packet(counter=1), 0)
    hub.set_consent("pod-02", False, 1, "k.smith")
    with pytest.raises(Rejected):
        hub.accept(packet(counter=2), 2)
    assert hub.rejections["no_consent"] == 1


def test_storage_fault_is_visible_but_reception_continues(hub):
    hub.store.set_fault(True)
    hub.accept(packet(counter=1, mid="a"), 0)
    hub.flush(); time.sleep(0.05)
    assert hub.storage_fault and "Storage fault" in hub.storage_fault
    assert hub.accept(packet(counter=2, mid="b", alert="trend"), 1)
    assert hub.state("pod-02").alert == "trend"
    hub.store.set_fault(False)
    hub.accept(packet(counter=3, mid="c"), 2)
    hub.flush(); time.sleep(0.05)
    assert hub.storage_fault is None


def test_export_excludes_non_consenting_and_includes_consent_records():
    store, hub, accounts, roster, engine = build_demo(autoplay=False, seed_history=False)
    for _ in range(12):
        engine.step()
    hub.flush()
    csv_text = store.export_csv("2026-10-22T09:00:00", lambda t: str(t))
    assert "pod-33" not in [line.split(",")[1] for line in csv_text.splitlines() if line.startswith("measurement")]
    assert "consent,P33,33,none" in csv_text
    assert "measurement,pod-02" in csv_text


def test_load_30_sources(hub_seconds=120):
    """Scaled AC-E7: 30 sources x 1 msg/s; every unique message accepted,
    no misassignment. (Full test is 1 h / 108,000 messages.)"""
    store, hub, accounts, roster, engine = build_demo(autoplay=False, seed_history=False)
    for _ in range(hub_seconds):
        engine.step()
    expected = hub_seconds * (len(roster) - 1)                        # jersey 33 never consented
    assert hub.accepted == expected
    assert hub.rejections["no_consent"] == hub_seconds
    for m in roster:
        if m["jersey"] != 33:
            assert hub.state(m["device_id"]).last_counter == hub_seconds


# -- F: views -------------------------------------------------------------------------
def test_tile_priority_and_labels(hub):
    member = {"device_id": "pod-02", "jersey": 2, "name": "A. Rivera"}
    hub.accept(packet(counter=1, alert="trend", skin_contact=False, battery_pct=10), 0)
    tile = views.build_tile(hub, member, 0)
    assert tile["state"] == "trend" and tile["label"] == "Rising faster than team setting"
    assert set(tile["badges"]) >= {"detached", "low_battery"}
    hub.accept(packet(counter=2, mid="m2", alert="absolute", skin_contact=False, battery_pct=10), 1)
    assert views.build_tile(hub, member, 1)["label"] == "Above team temperature setting"
    hub.accept(packet(counter=3, mid="m3", alert=None, skin_contact=False, battery_pct=10), 2)
    assert views.build_tile(hub, member, 2)["state"] == "detached"
    assert views.build_tile(hub, member, 2 + NO_SIGNAL_AFTER_S)["label"] == f"No signal for {NO_SIGNAL_AFTER_S} s"
    hub.accept(packet(counter=4, mid="m4", skin_contact=True, battery_pct=10), 70)
    assert views.build_tile(hub, member, 70)["label"] == "Replace batteries"
    hub.set_consent("pod-02", False, 71)
    t = views.build_tile(hub, member, 71)
    assert t["label"] == "Not collecting: no consent" and t["skin_c"] is None


def test_alerts_only_hides_temperature_but_not_alert(hub):
    member = {"device_id": "pod-02", "jersey": 2, "name": "A. Rivera"}
    hub.set_alerts_only("pod-02", True)
    hub.accept(packet(counter=1, alert="absolute"), 0)
    tile = views.build_tile(hub, member, 0)
    assert tile["skin_c"] is None and tile["state"] == "absolute" and "alerts_only" in tile["badges"]


def test_strip_order_acknowledged_demoted(hub):
    hub.enroll("pod-04", consented=True)
    roster = [{"device_id": "pod-02", "jersey": 2, "name": "A"}, {"device_id": "pod-04", "jersey": 4, "name": "B"}]
    hub.accept(packet(dev="pod-02", counter=1, alert="trend"), 0)
    hub.accept(packet(dev="pod-04", counter=1, alert="absolute"), 5)
    hub.acknowledge("pod-04", "k.smith", 6)
    strip = views.build_alert_strip(hub, roster, 10)
    assert [r["device_id"] for r in strip] == ["pod-02", "pod-04"]    # acknowledged last, still listed
    assert strip[1]["acknowledged"] and strip[1]["ack_by"] == "k.smith"


def test_every_user_facing_string_passes_wording_audit():
    for s in views.all_user_facing_strings():
        low = s.lower()
        for bad in views.FORBIDDEN_WORDS:
            assert bad not in low, f"{bad!r} found in {s!r}"


def test_tiles_in_jersey_order_and_diff_only_changed(hub):
    hub.enroll("pod-04", consented=True)
    roster = [{"device_id": "pod-04", "jersey": 4, "name": "B"}, {"device_id": "pod-02", "jersey": 2, "name": "A"}]
    a = views.build_roster(hub, roster, 0)
    assert [t["jersey"] for t in a] == [2, 4]
    hub.accept(packet(dev="pod-04", counter=1), 0)
    b = views.build_roster(hub, roster, 0)
    assert [t["jersey"] for t in views.diff_tiles(a, b)] == [4]


# -- auth -----------------------------------------------------------------------------
def test_cross_athlete_access_denied_and_logged():
    acc = Accounts()
    acc.add("athlete11", "demo", "athlete", "Iris Lind", device_id="pod-11")
    acc.add("k.smith", "demo", "staff", "Krista Smith")
    assert acc.sign_in("athlete11", "wrong") is None
    tok = acc.sign_in("athlete11", "demo")
    u = acc.user_for(tok)
    acc.require_device_access(u, "pod-11", 1, "own")
    with pytest.raises(AccessDenied):
        acc.require_device_access(u, "pod-07", 2, "GET /api/wearer/pod-07")
    with pytest.raises(AccessDenied):
        acc.require_staff(u, 3, "GET /api/roster")
    assert [d[2] for d in acc.denied_log] == ["GET /api/wearer/pod-07", "GET /api/roster"]
    staff = acc.user_for(acc.sign_in("k.smith", "demo"))
    acc.require_device_access(staff, "pod-07", 4, "staff reads any wearer")


# -- synthetic pod decides its own alerts ----------------------------------------------
def test_pod_decides_trend_then_absolute_and_clears_off_skin():
    pod = SyntheticPod("pod-11", seed=1, base_skin_c=36.0)
    pod.rise_started_at = 0; pod.rise_cap_c = 3.5
    seen = set()
    for t in range(0, 200):
        seen.add(pod.broadcast(t, 30.0, 55.0)["alert"])
    assert {"trend", "absolute"} <= seen
    pod.skin_contact = False
    assert pod.broadcast(200, 30.0, 55.0)["alert"] is None
