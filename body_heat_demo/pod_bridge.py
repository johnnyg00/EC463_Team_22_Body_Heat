"""BLE bridge: the real TempMonitor pod (XIAO ESP32S3 + TMP117 + SHT45) into the hub.

The firmware package TempMonitor_v1.1.0_USB_Power_Fix/ (component-testing
branch) owns the radio contract. Its receiver.py is imported unchanged for the
UUIDs, packet decoders, duplicate/freshness watches and device selection. This
module only maps decoded notifications onto the packet the hub already accepts
and hands them to Engine.ingest_external. The real pod therefore goes through
the same ingest order as the synthetic pods (format, enrollment, auth, consent,
counter), then live state, hub analysis and storage.

Mapping: what the pod does not send, and what the bridge does instead.

  device_id       Not in the payload. One board feeds one roster slot chosen
                  with --ble-pod. Once selected, the bridge reconnects only to
                  that board, never silently to another nearby TempMonitor.
  session_id, tag The bench firmware has no pairing, encryption or app-level
                  authentication. The bridge, as the local collector, stamps
                  the hub's synthetic session and tag. These readings are NOT
                  authenticated. The real AEAD design is still [OPEN].
  counter         The pod's 16-bit sequence wraps and restarts on reboot. The
                  bridge unwraps it into a strictly increasing hub counter.
  measurement_id  Every sensor notification is a new 1 s measurement.
  battery_pct,    No battery gauge or IMU on this board. Sent as None (unknown),
  motion          never zero. The hub does not store missing values.
  skin_contact    No contact sensor. Sent as True, so "Pod not on skin" cannot
                  show for this pod.
  alert           The latest live alert event (10 s cadence) rides on each 1 s
                  sensor packet, as with the synthetic pods. Absolute outranks
                  trend. Awaiting a sample, fault, stale (no alert event for
                  15 s), no alert characteristic or a fresh connection all send
                  alert_valid=False. The hub then shows "Pod decision
                  unavailable" and holds the last decided alert: never OK, never
                  a cleared alert.
  slope           The pod's least-squares slope in C/min (only once its 5-min
                  window is ready), x 5 for the hub's C per 5 min.

The pod's absolute threshold (29.0 C demo setting) is shown from its alert
packets in the staff collector status. It is not hard-coded here.
"""

import asyncio
import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

POD_PACKAGE = Path(__file__).resolve().parent.parent / "TempMonitor_v1.1.0_USB_Power_Fix"
# Freshness policy from the firmware handoff (HUB_INTEGRATION.md), on the host monotonic clock.
SENSOR_STALE_S = 5
ALERT_STALE_S = 15
RETRY_S = 3.0


def load_protocol(path=POD_PACKAGE / "receiver.py"):
    """Import the firmware package's receiver.py. Importing it does not scan."""
    name = "tempmonitor_receiver"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. It is on the component-testing branch.")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module      # dataclasses look up their module while building classes
    spec.loader.exec_module(module)
    return module


_PROBE = "import asyncio\nfrom bleak import BleakScanner\nasyncio.run(BleakScanner.discover(timeout=1))"


def bluetooth_preflight(run=subprocess.run, platform=sys.platform, timeout=60):
    """On macOS, a process that touches Bluetooth without permission is killed
    outright (SIGABRT, no message), which would take the whole hub down. Probe in
    a child process first so the hub can explain instead. Returns None if the
    radio can be used, else what to do. A permission prompt, if macOS shows one,
    appears during this probe."""
    if platform != "darwin":
        return None
    try:
        result = run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None     # still waiting on a prompt or a slow adapter; the bridge retries on its own
    if result.returncode in (-6, 134):
        return ("macOS stopped Bluetooth access for the app that started this hub. Start it from Terminal "
                "(or iTerm) and allow it under System Settings > Privacy & Security > Bluetooth.")
    if result.returncode != 0:
        last = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
        return f"Bluetooth unavailable: {last}. Turn Bluetooth on and check its permission for this app."
    return None


def _alert_kind(alert):
    if alert.absolute:
        return "absolute"
    return "trend" if alert.trend else None


class PodBridge:
    """Mapping and link state for one real pod. No radio code, so it runs offline."""

    def __init__(self, protocol, device_id, session_id, clock=time.monotonic):
        self.p = protocol
        self.device_id = device_id
        self.session_id = session_id
        self.clock = clock
        self.link, self.link_detail = "starting", None
        self.ble_id = None
        self.alerts_supported = None
        self.connections = 0
        self.counter = 0
        self.forwarded = 0
        self.hub_rejected = 0
        self.last_hub_rejection = None
        self.bad_frames = {"sensor": 0, "alert": 0}
        self.pod_threshold_c = None
        self._new_link()

    def _new_link(self):
        """Freshness, duplicates and the alert decision belong to one connection.
        HTTP threads read status while the radio thread writes it, so each piece
        of state is one attribute swapped whole, never two read separately."""
        self._sensor_watch = self.p.StreamWatch(self.clock())
        self._alert_watch = self.p.AlertWatch()
        self._alert_state = (None, None)    # (latest live AlertReading, host time it arrived)
        self._snapshot_fault = False        # the stored value read on connect reports a fault
        self._last_seq = None
        self._last_sensor_at = None
        self._last_reading = None           # (Reading, measurement_id) from this connection

    # -- link events (from the radio loop) --------------------------------------
    def set_link(self, link, detail=None):
        self.link, self.link_detail = link, detail

    def connected(self, ble_id, alerts_supported):
        self.connections += 1
        self.ble_id = ble_id
        self.alerts_supported = alerts_supported
        self._new_link()
        self.set_link("connected")

    def disconnected(self, detail=None):
        self._new_link()
        self.set_link("disconnected", detail)

    def reconnect_due(self):
        return self._sensor_watch.reconnect_due(self.clock())

    # -- notifications ------------------------------------------------------------
    def on_alert(self, data):
        """One alert notification -> a hub packet carrying the new decision, or None."""
        try:
            alert = self.p.decode_alert_packet(data)
        except ValueError:
            self.bad_frames["alert"] += 1
            return None
        now = self.clock()
        if not self._alert_watch.observe(alert, now):
            return None
        self._alert_state = (alert, now)
        self.pod_threshold_c = alert.threshold_c
        if self._last_reading is None:
            return None     # nothing read on this connection yet; the next reading carries it
        # Carry the new decision to the hub now, not with the next reading (which
        # may be late): a rebroadcast of the last reading under the same
        # measurement_id, as the synthetic pods do. The hub stores nothing new and
        # the reading's age keeps growing; only the decision is fresh.
        reading, measurement_id = self._last_reading
        self.counter += 1
        return self._packet(reading, measurement_id, now)

    def on_snapshot(self, data):
        """The alert value read once on connect, before subscribing (as receiver.py
        does). It is a stored value, not a live decision: it never becomes the
        current decision. It only names a fault the firmware will not resend, and
        marks that event as seen so a repeat of it is not taken as live."""
        try:
            alert = self.p.decode_alert_packet(data)
        except ValueError:
            self.bad_frames["alert"] += 1
            return
        self._alert_watch.observe(alert, self.clock())
        self._snapshot_fault = alert.fault and not alert.valid
        self.pod_threshold_c = alert.threshold_c

    def on_sensor(self, data):
        """One sensor notification -> hub packet, or None if malformed or repeated."""
        try:
            reading = self.p.decode_packet(data)
        except ValueError:
            self.bad_frames["sensor"] += 1
            return None
        now = self.clock()
        if not self._sensor_watch.observe(reading, now):
            return None
        self._last_sensor_at = now
        counter = self._unwrap(reading.sequence)
        measurement_id = f"{self.device_id}-ble{counter}"
        self._last_reading = (reading, measurement_id)
        return self._packet(reading, measurement_id, now)

    def _packet(self, reading, measurement_id, now):
        status = self.alert_status(now)
        alert = self._alert_state[0] if status == "valid" else None
        slope = alert.slope_c_per_min if alert is not None else None
        return {
            "device_id": self.device_id, "session_id": self.session_id, "tag": "ok",
            "counter": self.counter, "measurement_id": measurement_id,
            "skin_c": reading.skin_c, "air_c": reading.ambient_c, "humidity_pct": reading.humidity_pct,
            "motion": None, "battery_pct": None, "skin_contact": True,
            "alert": _alert_kind(alert) if alert is not None else None,
            "alert_valid": status == "valid", "alert_status": status,
            # False while the pod's 5-min trend window fills: not a "not rising" result
            "trend_ready": alert.trend_ready if alert is not None else None,
            "slope_c_per_5min": None if slope is None else round(slope * 5, 3),
            "source": "ble", "pod_sequence": reading.sequence, "pod_uptime_ms": reading.uptime_ms,
        }

    def _unwrap(self, seq):
        step = 1 if self._last_seq is None else (seq - self._last_seq) & 0xFFFF
        if not 0 < step < 0x8000:
            step = 1    # repeated or backwards sequence, e.g. the pod restarted
        self._last_seq = seq
        self.counter += step
        return self.counter

    # -- status ---------------------------------------------------------------------
    def alert_status(self, now=None):
        now = self.clock() if now is None else now
        if self.link != "connected":
            return "disconnected"
        if self.alerts_supported is False:
            return "unsupported"
        alert, at = self._alert_state
        if alert is None:
            return "fault" if self._snapshot_fault else "awaiting_sample"
        if not alert.valid:
            return "fault" if alert.fault else "awaiting_sample"
        return "stale" if now - at >= ALERT_STALE_S else "valid"

    def sensor_status(self, now=None):
        now = self.clock() if now is None else now
        last = self._last_sensor_at
        if self.link != "connected":
            return "disconnected"
        if last is None:
            return "waiting"
        return "stale" if now - last >= SENSOR_STALE_S else "live"

    def status(self):
        now = self.clock()
        return {
            "device_id": self.device_id, "link": self.link, "detail": self.link_detail,
            "ble_id": self.ble_id, "sensor": self.sensor_status(now), "decision": self.alert_status(now),
            "pod_threshold_c": self.pod_threshold_c, "connections": self.connections,
            "forwarded": self.forwarded, "hub_rejected": self.hub_rejected,
            "last_hub_rejection": self.last_hub_rejection, "bad_frames": sum(self.bad_frames.values()),
        }


class UnsupportedFirmware(Exception):
    pass


async def run_radio(bridge, deliver, *, device=None, scan_seconds=8.0, stop=None, bleak=None,
                    retry_s=RETRY_S):
    """Scan, connect, subscribe and forward until `stop` (a threading.Event) is set.

    Follows receiver.run(): scan by service UUID, require an explicit --ble-device
    when several boards answer, stay with the selected board, read the stored
    alert value before subscribing, resubscribe after every connection, and
    reconnect after 15 s without a fresh sensor packet. Every second it also
    checks the pod's decision, so the hub hears at once when it goes stale even
    if no packet arrives to carry that news.

    deliver(kind, payload) runs in one worker thread, in arrival order, so the
    BLE callbacks stay short: ("packet", hub_packet) or ("unavailable", why).
    Any error ends only the current connection: the hub is told the decision is
    unavailable and the loop scans again, so the radio never silently stops."""
    if bleak is None:
        import bleak
    P = bridge.p
    stopped = (lambda: stop.is_set()) if stop is not None else (lambda: False)
    queue = asyncio.Queue()

    async def forward():
        while True:
            item = await queue.get()
            try:
                await asyncio.to_thread(deliver, *item)
            except Exception as e:      # never let one bad hand-off stop forwarding
                bridge.hub_rejected += 1
                bridge.last_hub_rejection = f"{type(e).__name__}: {e}"
            finally:
                queue.task_done()

    forwarder = asyncio.create_task(forward())
    target = device
    try:
        while not stopped():
            had_link, why = False, "disconnected"
            try:
                bridge.set_link("scanning", target)
                results = await bleak.BleakScanner.discover(
                    timeout=scan_seconds, return_adv=True, service_uuids=[P.SERVICE_UUID])
                found = [d for d, adv in results.values()
                         if P.SERVICE_UUID in {u.lower() for u in adv.service_uuids}]
                try:
                    chosen = P.choose_device(found, target)
                except P.SelectionError:
                    ids = ", ".join(d.address for d in found)
                    bridge.set_link("choose_board", f"several TempMonitor boards; restart with --ble-device ID ({ids})")
                    await asyncio.sleep(retry_s)
                    continue
                if chosen is None:
                    bridge.set_link("not_found", "no board found; check power and close other receivers")
                    await asyncio.sleep(retry_s)
                    continue
                target = chosen.address     # from now on, only this board
                lost = asyncio.Event()
                loop = asyncio.get_running_loop()
                bridge.set_link("connecting", target)
                async with bleak.BleakClient(chosen, disconnected_callback=lambda _c: loop.call_soon_threadsafe(lost.set),
                                             services=[P.SERVICE_UUID], timeout=20) as client:
                    data_char = client.services.get_characteristic(P.DATA_UUID)
                    if data_char is None or "notify" not in data_char.properties:
                        raise UnsupportedFirmware("no sensor characteristic; upload the TempMonitor sketch")
                    alert_char = client.services.get_characteristic(P.ALERT_UUID)
                    alerts_ok = alert_char is not None and "notify" in alert_char.properties
                    bridge.connected(target, alerts_ok)
                    had_link = True
                    flagged = None
                    if alerts_ok:
                        if "read" in alert_char.properties:
                            # Before subscribing: CoreBluetooth delivers reads and
                            # notifications through the same path once subscribed.
                            bridge.on_snapshot(await client.read_gatt_char(alert_char))

                    def forward_packet(decode):
                        def callback(_c, data):
                            pkt = decode(data)
                            if pkt is not None:
                                queue.put_nowait(("packet", pkt))
                        return callback

                    on_sensor = forward_packet(bridge.on_sensor)
                    if alerts_ok:
                        await client.start_notify(alert_char, forward_packet(bridge.on_alert))

                    await client.start_notify(data_char, on_sensor)
                    while not lost.is_set() and not stopped():
                        status = bridge.alert_status()
                        if status != "valid" and status != flagged:
                            queue.put_nowait(("unavailable", status))
                        flagged = None if status == "valid" else status
                        try:
                            await asyncio.wait_for(lost.wait(), timeout=1)
                        except asyncio.TimeoutError:
                            if bridge.reconnect_due():
                                raise TimeoutError("no fresh sensor packet for 15 s; reconnecting")
            except Exception as e:      # bleak, OS and unexpected errors alike: retry, never die
                why = str(e) or type(e).__name__
                if not had_link:
                    bridge.set_link("error", why)
            if had_link:
                bridge.disconnected(why)
                queue.put_nowait(("unavailable", "link lost"))
            if not stopped():
                await asyncio.sleep(retry_s)
    finally:
        await queue.join()
        forwarder.cancel()


def attach(engine, hub, device_id, *, ble_device=None, scan_seconds=8.0, protocol=None, bleak=None,
           stop=None, retry_s=RETRY_S, clock=time.monotonic):
    """Feed roster slot `device_id` from a real TempMonitor; starts the radio thread."""
    bridge = PodBridge(protocol or load_protocol(), device_id, hub.session_id, clock=clock)
    hub.collectors.append(bridge)

    def deliver(kind, payload):
        if kind == "packet":
            why = engine.ingest_external(payload)
            if why is None:
                bridge.forwarded += 1
            else:
                bridge.hub_rejected += 1
                bridge.last_hub_rejection = why
        else:
            engine.external_decision_unavailable(device_id, payload)

    def main():
        try:
            asyncio.run(run_radio(bridge, deliver, device=ble_device, scan_seconds=scan_seconds,
                                  stop=stop, bleak=bleak, retry_s=retry_s))
        except Exception as e:      # keep the hub running; show why the radio stopped
            bridge.set_link("error", f"radio loop stopped: {e}")

    thread = threading.Thread(target=main, daemon=True, name=f"ble-{device_id}")
    thread.start()
    return bridge, thread
