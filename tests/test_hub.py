"""Hub validation rules the views rely on (mirrors Subsystem E acceptance)."""

import pytest

from f1_sideline_demo.hub import Hub, Rejected, NO_SIGNAL_AFTER_S


def packet(dev="pod-03", counter=1, mid="m1", alert=None):
    return {"device_id": dev, "counter": counter, "measurement_id": mid,
            "skin_c": 36.0, "air_c": 22.0, "humidity_pct": 45.0,
            "motion": 0.5, "battery_pct": 99.0, "alert": alert,
            "skin_contact": True}


def make_hub():
    hub = Hub()
    hub.enroll("pod-03", consented=True)
    hub.enroll("pod-07", consented=False)
    return hub


def test_unknown_device_rejected():
    hub = make_hub()
    with pytest.raises(Rejected):
        hub.accept(packet(dev="pod-99"), t=0)


def test_no_consent_rejected():
    hub = make_hub()
    with pytest.raises(Rejected):
        hub.accept(packet(dev="pod-07"), t=0)


def test_replayed_counter_rejected():
    hub = make_hub()
    assert hub.accept(packet(counter=1), t=0)
    with pytest.raises(Rejected):
        hub.accept(packet(counter=1, mid="m2"), t=1)
    with pytest.raises(Rejected):
        hub.accept(packet(counter=0, mid="m2"), t=1)


def test_repeated_measurement_creates_no_new_reading():
    hub = make_hub()
    hub.accept(packet(counter=1, mid="m1"), t=0)
    hub.state("pod-03").reading["skin_c"] = 36.0
    # Same measurement rebroadcast 1 s later with a fresh counter (1 Hz broadcast).
    hub.accept(packet(counter=2, mid="m1"), t=1)
    assert hub.state("pod-03").reading["skin_c"] == 36.0
    # New measurement id does update the reading.
    hub.accept(packet(counter=3, mid="m2"), t=2)
    assert hub.state("pod-03").last_measurement_id == "m2"


def test_no_signal_after_60_seconds():
    hub = make_hub()
    hub.accept(packet(counter=1), t=100)
    assert not hub.no_signal("pod-03", t=100 + NO_SIGNAL_AFTER_S - 1)
    assert hub.no_signal("pod-03", t=100 + NO_SIGNAL_AFTER_S)


def test_acknowledgement_is_recorded_and_does_not_clear():
    hub = make_hub()
    hub.accept(packet(counter=1, alert="trend"), t=0)
    hub.acknowledge("pod-03", user="trainer", t=5)
    assert hub.state("pod-03").alert == "trend"  # only the pod clears it
    assert hub.events[-1] == (5, "ack", "trainer acknowledged pod-03")
