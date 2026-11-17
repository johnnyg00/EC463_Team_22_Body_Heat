"""BLE bridge: real TempMonitor notifications -> hub, offline.

Golden byte strings are the verified decoder examples in
TempMonitor_v1.1.0_USB_Power_Fix/BLE_PROTOCOL.md. The fake radio stands in for
bleak so the scan/connect/subscribe/reconnect loop runs without hardware; it
does not replace a live test with the board."""
import asyncio
import threading
import types

import pytest

from body_heat_demo import pod_bridge, views
from body_heat_demo.engine import build_demo
from body_heat_demo.pod_bridge import PodBridge

P = pod_bridge.load_protocol()
DEV = "pod-02"

SENSOR_VALID = bytes.fromhex("01 03 2a 00 40 e2 01 00 76 0b 39 09 10 09")
SENSOR_WAIT = bytes.fromhex("01 06 2b 00 28 e6 01 00 00 80 39 09 10 09")
ALERT_ABS_WARMUP = bytes.fromhex("01 09 07 00 6c e2 01 00 6c e2 01 00 54 0b 00 00 00 80 04")
ALERT_BOTH_ON = bytes.fromhex("01 0f 1f 00 e0 93 04 00 e0 93 04 00 54 0b 50 00 00 00 1f")
ALERT_FAULT = bytes.fromhex("01 10 20 00 bc 99 04 00 ff ff ff ff 54 0b 00 00 00 80 00")
ALERT_RESET = bytes.fromhex("01 00 21 00 b0 9b 04 00 ff ff ff ff 54 0b 00 00 00 80 00")


def sensor(seq, skin=36.0, air=24.0, rh=40.0, uptime=None):
    uptime = 1000 * seq if uptime is None else uptime
    return P.PACKET.pack(1, 0x03, seq, uptime, round(skin * 100), round(air * 100), round(rh * 100))


def alert(seq, absolute=False, trend=False, count=5, slope=None):
    flags = 0x08 | absolute | (trend << 1) | ((count == 31) << 2)
    report = 10000 * seq
    return P.ALERT_PACKET.pack(1, flags, seq, report, report, 2900,
                               P.INVALID_SLOPE if slope is None else round(slope * 1000), count)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def bridge():
    b = PodBridge(P, DEV, "S-test", clock=Clock())
    b.connected("AA-11", alerts_supported=True)
    return b


# -- mapping ------------------------------------------------------------------------
def test_golden_packets_map_onto_hub_packet(bridge):
    pkt = bridge.on_sensor(SENSOR_VALID)
    assert pkt["skin_c"] == 29.34 and pkt["air_c"] == 23.61 and pkt["humidity_pct"] == 23.2
    assert pkt["motion"] is None and pkt["battery_pct"] is None and pkt["skin_contact"] is True
    assert (pkt["device_id"], pkt["session_id"], pkt["tag"], pkt["counter"]) == (DEV, "S-test", "ok", 1)
    # No live alert event yet on this connection: no decision, not OK.
    assert (pkt["alert"], pkt["alert_valid"], pkt["alert_status"]) == (None, False, "awaiting_sample")

    bridge.on_alert(ALERT_ABS_WARMUP)
    pkt = bridge.on_sensor(SENSOR_WAIT)
    assert pkt["skin_c"] is None and pkt["air_c"] == 23.61         # TMP117 WAIT stays null
    assert (pkt["alert"], pkt["alert_valid"], pkt["slope_c_per_5min"]) == ("absolute", True, None)
    assert bridge.pod_threshold_c == 29.0

    bridge.on_alert(ALERT_BOTH_ON)
    pkt = bridge.on_sensor(sensor(44))
    assert (pkt["alert"], pkt["slope_c_per_5min"]) == ("absolute", 0.4)   # 0.080 C/min x 5

    bridge.on_alert(ALERT_FAULT)
    pkt = bridge.on_sensor(sensor(45))
    assert (pkt["alert"], pkt["alert_valid"], pkt["alert_status"]) == (None, False, "fault")

    bridge.on_alert(ALERT_RESET)
    assert bridge.on_sensor(sensor(46))["alert_status"] == "awaiting_sample"


def test_trend_only_and_valid_off(bridge):
    bridge.on_alert(alert(1, trend=True, count=31, slope=0.07))
    pkt = bridge.on_sensor(sensor(1))
    assert (pkt["alert"], pkt["alert_valid"], pkt["slope_c_per_5min"], pkt["trend_ready"]) == ("trend", True, 0.35, True)
    bridge.on_alert(alert(2, count=6))      # valid, absolute off, trend window still filling
    pkt = bridge.on_sensor(sensor(2))
    assert (pkt["alert"], pkt["alert_valid"], pkt["slope_c_per_5min"], pkt["trend_ready"]) == (None, True, None, False)


def test_new_decision_is_forwarded_at_once_as_a_rebroadcast(bridge):
    assert bridge.on_alert(alert(1)) is None            # no reading on this link yet
    first = bridge.on_sensor(sensor(1, skin=29.5))
    pkt = bridge.on_alert(alert(2, absolute=True))
    assert pkt["alert"] == "absolute" and pkt["alert_valid"] is True
    assert pkt["measurement_id"] == first["measurement_id"] and pkt["skin_c"] == 29.5   # same reading
    assert pkt["counter"] == first["counter"] + 1
    assert bridge.on_alert(alert(2, absolute=True)) is None     # repeated event: nothing new
    assert bridge.on_sensor(sensor(2))["counter"] == pkt["counter"] + 1
    bridge.connected("AA-11", alerts_supported=True)
    assert bridge.on_alert(alert(3)) is None            # never resend a previous link's reading


def test_snapshot_names_a_persistent_fault_but_is_never_a_decision(bridge):
    bridge.on_snapshot(ALERT_FAULT)             # firmware does not resend a persistent fault
    assert bridge.on_sensor(sensor(1))["alert_status"] == "fault"
    bridge.connected("AA-11", alerts_supported=True)
    bridge.on_snapshot(alert(4, absolute=True))     # stored valid decision: not current
    pkt = bridge.on_sensor(sensor(2))
    assert (pkt["alert"], pkt["alert_valid"], pkt["alert_status"]) == (None, False, "awaiting_sample")
    bridge.on_alert(alert(4, absolute=True))    # the same event again is not live either
    assert bridge.alert_status() == "awaiting_sample"
    bridge.on_alert(alert(5, absolute=True))    # a new event is
    assert bridge.on_sensor(sensor(3))["alert"] == "absolute"


def test_alert_goes_stale_while_readings_continue(bridge):
    bridge.on_alert(alert(1, absolute=True))
    bridge.clock.now += 14.9
    assert bridge.on_sensor(sensor(1))["alert_valid"] is True
    bridge.clock.now += 0.1
    pkt = bridge.on_sensor(sensor(2))
    assert (pkt["alert"], pkt["alert_valid"], pkt["alert_status"]) == (None, False, "stale")
    bridge.on_alert(alert(1, absolute=True))       # a repeated event does not refresh it
    assert bridge.alert_status() == "stale"


def test_duplicates_malformed_wrap_and_reconnect(bridge):
    assert bridge.on_sensor(sensor(65535))["counter"] == 1
    assert bridge.on_sensor(sensor(65535)) is None                 # repeated notification
    assert bridge.on_sensor(SENSOR_VALID[:-1]) is None             # truncated frame
    bridge.on_alert(ALERT_FAULT[:1] + b"\xd0" + ALERT_FAULT[2:])  # reserved flag bits set
    assert bridge.bad_frames == {"sensor": 1, "alert": 1}
    assert bridge.on_sensor(sensor(0, uptime=65536000))["counter"] == 2   # 16-bit wrap
    assert bridge.on_sensor(sensor(3))["counter"] == 5                   # gap of 3 kept
    assert bridge.on_sensor(sensor(1))["counter"] == 6                   # backwards: e.g. reboot

    assert bridge.on_alert(alert(1, absolute=True))["counter"] == 7     # rebroadcast with the decision
    bridge.disconnected("link lost")
    assert bridge.status()["decision"] == "disconnected" and bridge.status()["sensor"] == "disconnected"
    bridge.connected("AA-11", alerts_supported=True)
    pkt = bridge.on_sensor(sensor(1))       # dedup and alert decision are per connection
    assert pkt["counter"] == 8 and pkt["alert_status"] == "awaiting_sample"


def test_sensor_only_firmware_is_unsupported_not_ok(bridge):
    bridge.connected("AA-11", alerts_supported=False)
    assert bridge.on_sensor(sensor(1))["alert_status"] == "unsupported"


# -- through the hub -------------------------------------------------------------------
def live_demo():
    store, hub, accounts, roster, engine = build_demo(autoplay=False, seed_history=False,
                                                      external_devices=[DEV])
    b = PodBridge(P, DEV, hub.session_id, clock=Clock())
    hub.collectors.append(b)
    b.connected("AA-11", alerts_supported=True)
    member = next(m for m in roster if m["device_id"] == DEV)
    return store, hub, engine, b, member


def feed(engine, b, data):
    pkt = b.on_sensor(data)
    assert pkt is not None
    assert engine.ingest_external(pkt) is None


def test_real_pod_states_through_hub():
    store, hub, engine, b, member = live_demo()
    for _ in range(3):
        engine.step()
    assert hub.state(DEV).last_accepted_at is None        # no synthetic packets for this slot

    feed(engine, b, sensor(1, skin=31.2))
    tile = views.build_tile(hub, member, engine.t)
    assert tile["state"] == "no_decision" and tile["skin_c"] == 31.2 and tile["live_pod"]
    assert tile["battery_pct"] is None and "low_battery" not in tile["badges"]

    b.on_alert(alert(1, absolute=True))
    feed(engine, b, sensor(2, skin=31.3))
    assert views.build_tile(hub, member, engine.t)["state"] == "absolute"

    b.on_alert(ALERT_FAULT)        # fault: hold the alert, flag that it is not current
    feed(engine, b, sensor(3))
    tile = views.build_tile(hub, member, engine.t)
    assert tile["state"] == "absolute" and "no_decision" in tile["badges"] and "resume" not in tile["badges"]
    assert views.build_alert_strip(hub, [member], engine.t)[0]["decision_unavailable"] is True

    b.on_alert(alert(2))           # valid OFF: only now does the pod clear it
    feed(engine, b, sensor(4, skin=28.0))
    tile = views.build_tile(hub, member, engine.t)
    assert tile["state"] == "ok" and "resume" in tile["badges"]
    assert "trend_warmup" in tile["badges"]                 # OK covers the limit only, for now
    b.on_alert(alert(3, count=31, slope=0.01))
    feed(engine, b, sensor(5, skin=28.0))
    assert "trend_warmup" not in views.build_tile(hub, member, engine.t)["badges"]

    engine.step()                  # a new session second, so the analyzer would see the packet
    feed(engine, b, SENSOR_WAIT)   # TMP117 not reading: no value, no analysis notice
    tile = views.build_tile(hub, member, engine.t)
    assert tile["skin_c"] is None and tile["hub_analysis"]["notice"] is None

    hub.flush()
    events = [(e["kind"], e["detail"]) for e in store.events(device_id=DEV)][::-1]
    assert [e for e in events if e[0] in ("alert", "alert_clear")] == [
        ("alert", "absolute"), ("alert_clear", "pod cleared absolute")]
    assert ("decision", "pod decision unavailable (fault)") in events
    signals = {sig for sig, *_ in store.history(DEV)}
    assert signals == {"skin_c", "air_c", "humidity_pct"}            # nothing stored as motion/zero
    assert len(store.history(DEV, "skin_c")) == 5                     # the WAIT packet stored no skin row

    accepted_at = hub.state(DEV).last_accepted_at
    engine.external_decision_unavailable(DEV, "link lost")
    assert views.build_tile(hub, member, engine.t)["state"] == "no_decision"
    assert hub.state(DEV).last_accepted_at == accepted_at             # not a reading

    staff = views.build_status(hub, engine.t, store)
    assert staff["collectors"][0]["device_id"] == DEV
    assert "collectors" not in views.build_status(hub, engine.t, store, staff=False)


def test_deterministic_analyzer_runs_on_real_pod_readings():
    store, hub, engine, b, member = live_demo()
    b.on_alert(alert(1))
    for seq in range(1, 31):
        feed(engine, b, sensor(seq, skin=39.0))
        engine.step()
    analysis = views.build_tile(hub, member, engine.t)["hub_analysis"]
    assert analysis["notice"] is None
    assert analysis["flag"]["kind"] == "above_limit"     # hub's own 38.5 C team setting


def test_consent_withdrawal_and_export_for_real_pod():
    store, hub, engine, b, member = live_demo()
    b.on_alert(alert(1))
    feed(engine, b, sensor(1, skin=30.5))
    hub.flush()
    rows = [r.split(",") for r in store.export_csv("start", str).splitlines() if r.startswith("measurement,pod-02")]
    assert sorted(r[4] for r in rows) == ["air_c", "humidity_pct", "skin_c"] and all(r[5] for r in rows)

    hub.set_consent(DEV, False, engine.t, "k.smith")
    store.set_consent(member["participant_id"], False, "later")
    assert engine.ingest_external(b.on_sensor(sensor(2))) == "no consent for pod-02"
    assert hub.rejections["no_consent"] == 1
    assert any(n["kind"] == "stop_wearable" for n in views.build_notices(hub, [member], engine.t))
    assert "measurement,pod-02" not in store.export_csv("start", str)


def test_bluetooth_preflight_explains_instead_of_crashing():
    def child(code):
        return lambda *a, **k: types.SimpleNamespace(
            returncode=code, stderr="Traceback ...\nBleakBluetoothNotAvailableError: Bluetooth is turned off\n")
    assert pod_bridge.bluetooth_preflight(run=child(-6), platform="darwin").startswith("macOS stopped")
    assert "turned off" in pod_bridge.bluetooth_preflight(run=child(1), platform="darwin")
    assert pod_bridge.bluetooth_preflight(run=child(0), platform="darwin") is None
    assert pod_bridge.bluetooth_preflight(run=child(-6), platform="linux") is None


@pytest.mark.parametrize("argv, message", [(["--ble-device", "X"], "needs --ble-pod"),
                                            (["--ble-pod", "6"], "not a roster jersey")])
def test_server_rejects_bad_ble_arguments(argv, message, capsys):
    from body_heat_demo import server
    with pytest.raises(SystemExit):
        server.main(argv)
    assert message in capsys.readouterr().err


def test_server_main_wires_the_ble_slot(monkeypatch):
    pytest.importorskip("bleak")    # the BLE path needs Python 3.10+ with bleak installed
    from body_heat_demo import server
    calls = {}
    monkeypatch.setattr(pod_bridge, "bluetooth_preflight", lambda: None)
    monkeypatch.setattr(pod_bridge, "attach", lambda engine, hub, dev, **kw: calls.update(
        dev=dev, kw=kw, external=set(engine.external)))

    class NoServe:
        def __init__(self, *a):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "ThreadingHTTPServer", NoServe)
    server.main(["--ble-pod", "2", "--ble-device", "BOARD-1", "--no-autoplay"])
    assert calls["dev"] == DEV and calls["external"] == {DEV}
    assert calls["kw"]["ble_device"] == "BOARD-1" and calls["kw"]["protocol"] is P
    assert server.STATE["store"].history(DEV) == []         # no fake history for the real slot


def test_external_slot_gets_no_fake_history_and_must_be_on_roster():
    store, *_ = build_demo(autoplay=False, external_devices=[DEV])
    assert store.history(DEV) == [] and store.history("pod-03")
    with pytest.raises(ValueError):
        build_demo(autoplay=False, seed_history=False, external_devices=["pod-99"])


# -- fake radio ------------------------------------------------------------------------------
def fake_bleak(script, stop, scans=(("AA-11",),), alerts=True, snapshot=ALERT_RESET, clock=None,
               fail_first_subscribe=False):
    """Stand-in for bleak. `scans` lists the boards each scan finds (the last one
    repeats). Script steps: ("sensor"|"alert", bytes), ("lose", None) drops the
    link, ("clock", t) sets the host clock, ("wait", s) idles s real seconds."""
    log = {"connects": [], "subscribed": [], "reads": 0}
    steps, scan_no = iter(script), [0]

    class Device:
        def __init__(self, address):
            self.address, self.name = address, "TempMonitor"

    class Scanner:
        @staticmethod
        async def discover(timeout, return_adv, service_uuids):
            boards = scans[min(scan_no[0], len(scans) - 1)]
            scan_no[0] += 1
            adv = types.SimpleNamespace(service_uuids=[P.SERVICE_UUID.upper()], local_name="TempMonitor")
            return {a: (Device(a), adv) for a in boards}

    class Char:
        def __init__(self, uuid):
            self.uuid, self.properties = uuid, ["read", "notify"]

    class Client:
        def __init__(self, device, disconnected_callback, services, timeout):
            self.device, self.lost, self.cbs = device, disconnected_callback, {}
            self.services = types.SimpleNamespace(get_characteristic=lambda u: Char(u) if (
                u == P.DATA_UUID or (alerts and u == P.ALERT_UUID)) else None)

        async def __aenter__(self):
            log["connects"].append(self.device.address)
            return self

        async def __aexit__(self, *exc):
            return False

        async def read_gatt_char(self, char):
            assert char.uuid not in self.cbs, "read the stored value before subscribing"
            log["reads"] += 1
            return snapshot

        async def start_notify(self, char, cb):
            if fail_first_subscribe and char.uuid == P.DATA_UUID and len(log["connects"]) == 1:
                raise RuntimeError("unexpected stack error")
            self.cbs[char.uuid] = cb
            log["subscribed"].append(char.uuid)
            if char.uuid == P.DATA_UUID:
                asyncio.get_running_loop().create_task(self.play())

        async def play(self):
            for kind, data in steps:
                if kind == "lose":
                    self.lost(self)
                    return
                if kind == "clock":
                    clock.now = data
                elif kind == "wait":
                    await asyncio.sleep(data)
                else:
                    self.cbs[P.DATA_UUID if kind == "sensor" else P.ALERT_UUID](None, data)
                await asyncio.sleep(0.01)
            stop.set()
            self.lost(self)

    module = types.SimpleNamespace(BleakScanner=Scanner, BleakClient=Client)
    return module, log


def radio_demo():
    store, hub, accounts, roster, engine = build_demo(autoplay=False, seed_history=False,
                                                      external_devices=[DEV])
    return store, hub, engine, next(m for m in roster if m["device_id"] == DEV)


def pod_events(store, hub):
    hub.flush()
    return [(e["kind"], e["detail"]) for e in store.events(device_id=DEV)][::-1]


def test_fake_radio_connects_subscribes_reconnects_and_reports_link_loss():
    store, hub, engine, member = radio_demo()
    stop = threading.Event()
    script = [("alert", alert(1, absolute=True)), ("sensor", sensor(1)), ("sensor", sensor(2)), ("lose", None),
              ("alert", alert(2)), ("sensor", sensor(3, skin=30.0)), ("sensor", sensor(4, skin=30.1))]
    radio, log = fake_bleak(script, stop)
    b, thread = pod_bridge.attach(engine, hub, DEV, protocol=P, bleak=radio, stop=stop, retry_s=0.01)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert log["connects"] == ["AA-11", "AA-11"] and log["reads"] == 2
    assert log["subscribed"] == [P.ALERT_UUID, P.DATA_UUID] * 2      # resubscribed both
    assert b.forwarded == 4 and b.hub_rejected == 0 and b.connections == 2
    assert hub.state(DEV).reading["skin_c"] == 30.1
    events = pod_events(store, hub)
    assert ("alert", "absolute") in events and ("alert_clear", "pod cleared absolute") in events
    assert ("decision", "pod decision unavailable (link lost)") in events
    assert views.build_tile(hub, member, engine.t)["state"] == "no_decision"   # stopped = link lost


def test_fake_radio_flags_a_stale_decision_while_the_link_stays_up():
    store, hub, engine, member = radio_demo()
    stop, clock = threading.Event(), Clock()
    clock.now = 0.0
    script = [("alert", alert(1)), ("sensor", sensor(1))]
    for second in range(1, 10):     # readings for 9 s after the decision, then nothing
        script += [("clock", float(second)), ("sensor", sensor(1 + second))]
    script += [("clock", 16.0), ("wait", 1.5)]      # decision 16 s old, last reading 7 s old
    radio, log = fake_bleak(script, stop, clock=clock)
    b, thread = pod_bridge.attach(engine, hub, DEV, protocol=P, bleak=radio, stop=stop, retry_s=0.01, clock=clock)
    thread.join(timeout=5)
    events = pod_events(store, hub)
    decisions = [d for k, d in events if k == "decision"]
    assert "pod decision unavailable (stale)" in decisions
    assert decisions.index("pod decision unavailable (stale)") < decisions.index("pod decision unavailable (link lost)")
    assert log["connects"] == ["AA-11"]         # the alert went stale before any reconnect was due


def test_fake_radio_delivers_a_new_alert_while_readings_stall():
    store, hub, engine, member = radio_demo()
    stop, clock = threading.Event(), Clock()
    clock.now = 0.0
    seen = {}
    script = [("alert", alert(1)), ("sensor", sensor(1, skin=28.9)),
              ("clock", 8.0), ("alert", alert(2, absolute=True)),   # readings stalled 8 s; link up
              ("wait", 0.3)]
    radio, log = fake_bleak(script, stop, clock=clock)
    original = engine.ingest_external

    def spy(pkt):
        why = original(pkt)
        seen[pkt["counter"]] = views.build_tile(hub, member, engine.t)["state"]
        return why

    engine.ingest_external = spy
    b, thread = pod_bridge.attach(engine, hub, DEV, protocol=P, bleak=radio, stop=stop, retry_s=0.01, clock=clock)
    thread.join(timeout=5)
    assert list(seen.values()) == ["ok", "absolute"]        # reached the card before any reconnect
    assert log["connects"] == ["AA-11"]
    assert len(store.history(DEV, "skin_c")) == 1           # the rebroadcast stored nothing new


def test_fake_radio_recovers_from_an_unexpected_error():
    store, hub, engine, member = radio_demo()
    stop = threading.Event()
    radio, log = fake_bleak([("alert", alert(1)), ("sensor", sensor(1, skin=30.0))], stop,
                            fail_first_subscribe=True)
    b, thread = pod_bridge.attach(engine, hub, DEV, protocol=P, bleak=radio, stop=stop, retry_s=0.01)
    thread.join(timeout=5)
    assert log["connects"] == ["AA-11", "AA-11"]        # retried instead of stopping for good
    assert b.forwarded == 1 and hub.state(DEV).reading["skin_c"] == 30.0
    assert "pod decision unavailable (link lost)" in [d for k, d in pod_events(store, hub) if k == "decision"]


def test_fake_radio_reports_a_persistent_fault_after_reconnect():
    store, hub, engine, member = radio_demo()
    stop = threading.Event()
    radio, log = fake_bleak([("sensor", sensor(1)), ("wait", 1.2)], stop, snapshot=ALERT_FAULT)
    b, thread = pod_bridge.attach(engine, hub, DEV, protocol=P, bleak=radio, stop=stop, retry_s=0.01)
    thread.join(timeout=5)
    assert "pod decision unavailable (fault)" in [d for k, d in pod_events(store, hub) if k == "decision"]


def test_fake_radio_never_picks_between_two_boards():
    _, hub, engine, _ = radio_demo()
    stop = threading.Event()
    radio, log = fake_bleak([], stop, scans=(("AA-11", "BB-22"),))
    b, thread = pod_bridge.attach(engine, hub, DEV, protocol=P, bleak=radio, stop=stop, retry_s=0.01)
    threading.Timer(0.2, stop.set).start()
    thread.join(timeout=5)
    assert log["connects"] == [] and b.link == "choose_board" and "--ble-device" in b.link_detail


def test_fake_radio_never_switches_boards_after_a_drop():
    _, hub, engine, _ = radio_demo()
    stop = threading.Event()
    radio, log = fake_bleak([("sensor", sensor(1)), ("lose", None)], stop, scans=(("AA-11",), ("BB-22",)))
    b, thread = pod_bridge.attach(engine, hub, DEV, protocol=P, bleak=radio, stop=stop, retry_s=0.01)
    threading.Timer(0.3, stop.set).start()
    thread.join(timeout=5)
    assert log["connects"] == ["AA-11"] and b.link == "not_found"   # BB-22 is someone else's pod
