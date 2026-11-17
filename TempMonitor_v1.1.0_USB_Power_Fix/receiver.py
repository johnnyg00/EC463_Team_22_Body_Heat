#!/usr/bin/env python3
"""Receive TempMonitor BLE notifications; Python 3.10+, bleak 3.1.0.

Uses Bluetooth only: the USB cable may remain attached to supply power.
Run: python receiver.py   |   python receiver.py --list
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
import struct
import sys
import time

SERVICE_UUID = "71f10000-7a42-4d32-9ef4-01352c8f9a10"
DATA_UUID = "71f10001-7a42-4d32-9ef4-01352c8f9a10"
ALERT_UUID = "71f10002-7a42-4d32-9ef4-01352c8f9a10"
PACKET = struct.Struct("<BBHIhhH")
ALERT_PACKET = struct.Struct("<BBHIIhiB")
INVALID_TEMP = -32768
INVALID_RH = 65535
INVALID_SLOPE = -2147483648
INVALID_SAMPLE_TIME = 0xFFFFFFFF


@dataclass(frozen=True)
class Reading:
    sequence: int
    uptime_ms: int
    skin_c: float | None
    ambient_c: float | None
    humidity_pct: float | None
    skin_wait: bool


@dataclass(frozen=True)
class AlertReading:
    sequence: int
    report_uptime_ms: int
    sample_uptime_ms: int | None
    threshold_c: float
    slope_c_per_min: float | None
    sample_count: int
    absolute: bool
    trend: bool
    trend_ready: bool
    valid: bool
    fault: bool
    history_restarted: bool


def decode_alert_packet(data: bytes | bytearray) -> AlertReading:
    if len(data) != ALERT_PACKET.size:
        raise ValueError(f"expected {ALERT_PACKET.size} alert bytes, received {len(data)}")
    version, flags, seq, report, sample, threshold, slope, count = ALERT_PACKET.unpack(data)
    if version != 1 or flags & ~0x3F:
        raise ValueError("unsupported alert packet version or status bits")
    absolute, trend, ready, valid, fault, restarted = (bool(flags & (1 << bit)) for bit in range(6))
    if threshold == INVALID_TEMP:
        raise ValueError("alert threshold is missing")
    if valid:
        if fault or not 1 <= count <= 31 or ready != (count == 31):
            raise ValueError("inconsistent valid alert state or sample count")
        # The valid flag distinguishes a real 0xFFFFFFFF timestamp at wrap
        # from the sentinel used by invalid decisions.
        if ((report - sample) & 0xFFFFFFFF) >= 0x80000000:
            raise ValueError("alert sample timestamp is later than its report")
        if trend and not ready:
            raise ValueError("trend alert cannot be active during warmup")
        if ready == (slope == INVALID_SLOPE):
            raise ValueError("alert slope availability disagrees with trend readiness")
    elif (absolute or trend or ready or restarted or count != 0
          or sample != INVALID_SAMPLE_TIME or slope != INVALID_SLOPE):
        raise ValueError("unavailable alert state contains a purported decision")
    return AlertReading(seq, report, sample if valid else None, threshold / 100,
                        slope / 1000 if ready else None, count, absolute, trend,
                        ready, valid, fault, restarted)


def format_alert(reading: AlertReading) -> str:
    if not reading.valid:
        state = "FAULT: decision unavailable" if reading.fault else "AWAITING SAMPLE: decision unavailable"
        return f"[ALERT] {state}  threshold={reading.threshold_c:.2f}C"
    trend = "WARMUP" if not reading.trend_ready else ("ON" if reading.trend else "OFF")
    slope = "--" if reading.slope_c_per_min is None else f"{reading.slope_c_per_min:.3f}C/min"
    restart = "  HISTORY RESTARTED" if reading.history_restarted else ""
    return (f"[ALERT] ABS={'ON' if reading.absolute else 'OFF'}  TREND={trend}"
            f"  threshold={reading.threshold_c:.2f}C  samples={reading.sample_count}/31"
            f"  slope={slope}{restart}")


class AlertWatch:
    """Temperature packets cannot keep an old alert decision marked fresh."""
    def __init__(self):
        self.last_reading: AlertReading | None = None
        self.last_event_at = self.last_warning_at = 0.0

    def observe(self, reading: AlertReading, now: float) -> bool:
        previous = self.last_reading
        if previous is not None and (reading.sequence, reading.report_uptime_ms) == (
                previous.sequence, previous.report_uptime_ms):
            return False
        self.last_reading = reading
        self.last_event_at = self.last_warning_at = now
        return True

    def warning_due(self, now: float) -> bool:
        if (self.last_reading is not None and self.last_reading.valid
                and now - self.last_event_at >= 15 and now - self.last_warning_at >= 15):
            self.last_warning_at = now
            return True
        return False


def decode_packet(data: bytes | bytearray) -> Reading:
    """Reject incomplete, unsupported, or inconsistent frames before display."""
    if len(data) != PACKET.size:
        raise ValueError(f"expected {PACKET.size} bytes, received {len(data)}")
    version, flags, seq, uptime, skin, ambient, humidity = PACKET.unpack(data)
    if version != 1:
        raise ValueError(f"unsupported packet version {version}")
    if flags & ~0x07:
        raise ValueError(f"unknown status bits 0x{flags:02X}")
    skin_valid, ambient_valid, skin_wait = bool(flags & 1), bool(flags & 2), bool(flags & 4)
    if skin_valid and skin_wait:
        raise ValueError("TMP117 cannot be valid and WAIT simultaneously")
    if skin_valid:
        if not -5500 <= skin <= 15000:
            raise ValueError("TMP117 value outside supported sensor range")
    elif skin != INVALID_TEMP:
        raise ValueError("invalid TMP117 value is missing its sentinel")
    if ambient_valid:
        if not -4500 <= ambient <= 13000 or not 0 <= humidity <= 10000:
            raise ValueError("SHT45 value outside supported sensor range")
    elif ambient != INVALID_TEMP or humidity != INVALID_RH:
        raise ValueError("invalid SHT45 values are missing their sentinels")
    return Reading(seq, uptime, skin / 100 if skin_valid else None,
                   ambient / 100 if ambient_valid else None,
                   humidity / 100 if ambient_valid else None, skin_wait)


def format_reading(reading: Reading) -> str:
    skin = f"{reading.skin_c:.2f}" if reading.skin_c is not None else (
        "WAIT" if reading.skin_wait else "ERR")
    ambient = f"{reading.ambient_c:.2f}" if reading.ambient_c is not None else "ERR"
    humidity = f"{reading.humidity_pct:.1f}" if reading.humidity_pct is not None else "ERR"
    return (f"TMP117_C={skin}  SHT45_C={ambient}  RH_pct={humidity}"
            f"  [BLE seq={reading.sequence}, uptime={reading.uptime_ms / 1000:.1f}s]")


class StreamWatch:
    """Track fresh packet arrival independently of the board's wrapping clock."""
    def __init__(self, now: float):
        self.last_packet_at = now
        self.last_warning_at = now
        self.last_identity: tuple[int, int] | None = None

    def observe(self, reading: Reading, now: float) -> bool:
        identity = (reading.sequence, reading.uptime_ms)
        if identity == self.last_identity:
            return False
        self.last_identity = identity
        self.last_packet_at = self.last_warning_at = now
        return True

    def warning_due(self, now: float) -> bool:
        if now - self.last_packet_at >= 5 and now - self.last_warning_at >= 5:
            self.last_warning_at = now
            return True
        return False

    def reconnect_due(self, now: float) -> bool:
        return now - self.last_packet_at >= 15


def log(message: str) -> None:
    stamp = datetime.now().astimezone().strftime("%H:%M:%S.%f")[:-3]
    print(f"{stamp} -> {message}", flush=True)


class SelectionError(Exception):
    pass


def choose_device(candidates: list, identifier: str | None):
    """Require an explicit device ID when several compatible boards are nearby."""
    if identifier:
        return next((d for d in candidates if d.address.casefold() == identifier.casefold()), None)
    if len(candidates) > 1:
        raise SelectionError("Several TempMonitor boards found. Run again with --device ID from the list above.")
    return candidates[0] if candidates else None


async def run(args: argparse.Namespace) -> None:
    try:
        from bleak import BleakClient, BleakScanner
        from bleak.exc import BleakBluetoothNotAvailableError, BleakError
    except ImportError as error:
        raise SystemExit("Install the receiver dependency first: python -m pip install -r requirements.txt") from error

    # Once a board is selected, reconnection stays with that board instead of
    # silently switching to a teammate's identically named device.
    target = args.device
    log("BLE receiver starting. Allow Terminal's Bluetooth permission if macOS asks. Ctrl+C stops.")
    while True:
        try:
            log("Scanning for TempMonitor..." if target is None else f"Scanning for device {target}...")
            results = await BleakScanner.discover(
                timeout=args.scan_seconds, return_adv=True, service_uuids=[SERVICE_UUID])
            candidates = []
            for device, adv in results.values():
                if SERVICE_UUID not in {u.lower() for u in adv.service_uuids}:
                    continue
                candidates.append(device)
                log(f"Found {adv.local_name or device.name or 'TempMonitor'} | ID: {device.address}")
            if args.list:
                if not candidates:
                    log("No matching boards found. Check power, uploaded sketch, and Bluetooth permissions.")
                return
            device = choose_device(candidates, target)
            if device is None:
                log("Board not found. Keep it powered and close other BLE clients; retrying in 3 seconds.")
                await asyncio.sleep(3)
                continue
            target = device.address
            disconnected = asyncio.Event()
            loop = asyncio.get_running_loop()

            def on_disconnect(_client) -> None:
                loop.call_soon_threadsafe(disconnected.set)

            log(f"Connecting to {device.address}...")
            async with BleakClient(device, disconnected_callback=on_disconnect,
                                   services=[SERVICE_UUID], timeout=20) as client:
                characteristic = client.services.get_characteristic(DATA_UUID)
                if characteristic is None or "notify" not in characteristic.properties:
                    raise SelectionError("Expected temperature notification characteristic is missing. Upload the supplied BLE sketch.")
                watch = StreamWatch(time.monotonic())

                def on_packet(_characteristic, data: bytearray) -> None:
                    try:
                        reading = decode_packet(data)
                    except ValueError as error:
                        log(f"Rejected malformed BLE packet: {error}")
                        return
                    if watch.observe(reading, time.monotonic()):
                        log(format_reading(reading))

                await client.start_notify(characteristic, on_packet)
                alert_watch = AlertWatch()
                alert_characteristic = client.services.get_characteristic(ALERT_UUID)
                if alert_characteristic is None:
                    log("Temperature-only firmware: no alert characteristic is present.")
                else:
                    if "notify" not in alert_characteristic.properties:
                        raise SelectionError("Alert characteristic does not support notifications. Upload the supplied alert firmware.")

                    def on_alert(_characteristic, data: bytearray) -> None:
                        try:
                            reading = decode_alert_packet(data)
                        except ValueError as error:
                            log(f"Rejected malformed alert packet: {error}")
                            return
                        if alert_watch.observe(reading, time.monotonic()):
                            log(format_alert(reading))

                    # Read before subscribing: CoreBluetooth treats reads and
                    # notifications alike once subscribed. Label this stored
                    # state explicitly; only later events establish live updates.
                    if "read" in alert_characteristic.properties:
                        try:
                            snapshot = decode_alert_packet(await client.read_gatt_char(alert_characteristic))
                            alert_watch.observe(snapshot, time.monotonic())
                            log(format_alert(snapshot) + "  [stored snapshot; awaiting live update]")
                        except ValueError as error:
                            log(f"Rejected malformed initial alert state: {error}")
                    await client.start_notify(alert_characteristic, on_alert)
                    log("Subscribed to alert updates; accepted samples normally arrive every 10 seconds.")
                log("Connected over BLE. Waiting for fresh sensor packets...")
                while not disconnected.is_set():
                    try:
                        await asyncio.wait_for(disconnected.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        now = time.monotonic()
                        if watch.warning_due(now):
                            log("NO NEW DATA: no fresh, valid BLE packet for at least 5 seconds.")
                        if alert_watch.warning_due(now):
                            log("[ALERT] STALE: no new alert event for at least 15 seconds; previous decision is not current.")
                        if watch.reconnect_due(now):
                            raise TimeoutError("No fresh BLE packets for 15 seconds; reconnecting.")
                log("BLE disconnected. Reconnecting to the same board...")
        except BleakBluetoothNotAvailableError as error:
            raise SystemExit(
                f"Bluetooth unavailable: {error}\nTurn on Bluetooth. On macOS allow Terminal under "
                "System Settings > Privacy & Security > Bluetooth, then rerun the receiver.") from error
        except SelectionError as error:
            raise SystemExit(str(error)) from error
        except (BleakError, OSError, TimeoutError) as error:
            if args.list:
                raise SystemExit(f"Bluetooth scan failed: {error}") from error
            log(f"Bluetooth connection issue: {error}. Retrying in 3 seconds.")
        await asyncio.sleep(3)


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required. Check with: python3 --version")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="exact device ID printed by --list (macOS uses a UUID)")
    parser.add_argument("--list", action="store_true", help="list only compatible advertising boards, then exit")
    parser.add_argument("--scan-seconds", type=float, default=8, help="scan duration; default 8 seconds")
    args = parser.parse_args()
    if not 1 <= args.scan_seconds <= 60:
        parser.error("--scan-seconds must be between 1 and 60")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nReceiver stopped.")


if __name__ == "__main__":
    main()
