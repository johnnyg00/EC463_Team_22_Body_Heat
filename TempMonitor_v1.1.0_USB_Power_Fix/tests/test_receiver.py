"""Host checks only; these do not exercise a Bluetooth adapter or sensor board."""
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from receiver import (AlertWatch, Reading, SelectionError, StreamWatch, choose_device,
                      decode_alert_packet, decode_packet, format_alert, format_reading)


def packet(flags=3, skin=2329, ambient=2357, rh=2370, seq=1, uptime=1000, version=1):
    return struct.pack("<BBHIhhH", version, flags, seq, uptime, skin, ambient, rh)


def alert_packet(flags=8, count=1, slope=-2147483648, sample=1000, report=1001,
                 seq=1, threshold=2900, version=1):
    return struct.pack("<BBHIIhiB", version, flags, seq, report, sample, threshold, slope, count)


class DecoderTests(unittest.TestCase):
    def test_golden_little_endian_packet(self):
        result = decode_packet(bytes.fromhex("0103341204030201190935094209"))
        self.assertEqual(result, Reading(0x1234, 0x01020304, 23.29, 23.57, 23.7, False))

    def test_zero_is_valid(self):
        result = decode_packet(packet(skin=0, ambient=0, rh=0))
        self.assertIn("TMP117_C=0.00", format_reading(result))
        self.assertEqual(result.humidity_pct, 0)

    def test_negative_and_upper_bound_values(self):
        self.assertEqual(decode_packet(packet(skin=-5500, ambient=-4500, rh=10000)).skin_c, -55)
        self.assertEqual(decode_packet(packet(skin=15000, ambient=13000)).ambient_c, 130)

    def test_wait_keeps_other_sensor(self):
        result = decode_packet(packet(flags=6, skin=-32768))
        self.assertIsNone(result.skin_c)
        self.assertTrue(result.skin_wait)
        self.assertIn("TMP117_C=WAIT", format_reading(result))
        self.assertEqual(result.ambient_c, 23.57)

    def test_each_sensor_can_fail_independently(self):
        self.assertIsNone(decode_packet(packet(flags=2, skin=-32768)).skin_c)
        result = decode_packet(packet(flags=1, ambient=-32768, rh=65535))
        self.assertIsNone(result.ambient_c)
        self.assertEqual(result.skin_c, 23.29)

    def test_all_invalid_initial_packet(self):
        result = decode_packet(packet(flags=0, skin=-32768, ambient=-32768, rh=65535))
        self.assertEqual(format_reading(result).count("ERR"), 3)

    def test_length_and_version(self):
        for malformed in (b"", packet()[:-1], packet() + b"\0", packet(version=2)):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                decode_packet(malformed)

    def test_inconsistent_flags_sentinels_and_ranges(self):
        malformed = [packet(flags=8), packet(flags=7), packet(skin=-32768),
                     packet(ambient=-32768), packet(rh=65535), packet(flags=2),
                     packet(flags=1), packet(flags=0, skin=-32768, ambient=-32768, rh=0),
                     packet(skin=15001), packet(skin=-5501), packet(ambient=13001),
                     packet(ambient=-4501), packet(rh=10001)]
        for data in malformed:
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_packet(data)


class FreshnessTests(unittest.TestCase):
    def test_warn_after_five_and_reconnect_after_fifteen(self):
        watch = StreamWatch(100)
        self.assertFalse(watch.warning_due(104.999))
        self.assertTrue(watch.warning_due(105))
        self.assertFalse(watch.warning_due(106))
        self.assertTrue(watch.warning_due(110))
        self.assertFalse(watch.reconnect_due(114.999))
        self.assertTrue(watch.reconnect_due(115))

    def test_duplicate_does_not_keep_dead_stream_alive(self):
        watch = StreamWatch(0)
        reading = decode_packet(packet())
        self.assertTrue(watch.observe(reading, 1))
        self.assertFalse(watch.observe(reading, 5))
        self.assertTrue(watch.warning_due(6))

    def test_new_packet_wrap_or_restart_restores_freshness(self):
        watch = StreamWatch(0)
        self.assertTrue(watch.observe(decode_packet(packet(seq=65535, uptime=0xFFFFFFFF)), 1))
        self.assertTrue(watch.observe(decode_packet(packet(seq=0, uptime=0)), 10))
        self.assertFalse(watch.warning_due(14.99))
        self.assertTrue(watch.warning_due(15))


class SelectionTests(unittest.TestCase):
    def test_none_and_one(self):
        device = SimpleNamespace(address="ABC")
        self.assertIsNone(choose_device([], None))
        self.assertIs(choose_device([device], None), device)

    def test_multiple_requires_explicit_id(self):
        a, b = SimpleNamespace(address="ABC"), SimpleNamespace(address="DEF")
        with self.assertRaises(SelectionError):
            choose_device([a, b], None)
        self.assertIs(choose_device([a, b], "def"), b)

    def test_reconnect_does_not_select_another_board(self):
        other = SimpleNamespace(address="DEF")
        self.assertIsNone(choose_device([other], "ABC"))


class AlertDecoderTests(unittest.TestCase):
    def test_golden_absolute_alert_while_trend_warms(self):
        # v1 flags9 seq0x1234 report1001 sample1000 threshold2900 slopeINT_MIN count1.
        result = decode_alert_packet(bytes.fromhex("01093412e9030000e8030000540b0000008001"))
        self.assertTrue(result.absolute)
        self.assertTrue(result.valid)
        self.assertFalse(result.trend_ready)
        self.assertEqual(result.threshold_c, 29)
        self.assertEqual(result.sequence, 0x1234)
        self.assertEqual(result.sample_uptime_ms, 1000)
        text = format_alert(result)
        self.assertIn("ABS=ON", text)
        self.assertIn("TREND=WARMUP", text)
        self.assertIn("samples=1/31", text)

    def test_ready_trend_on_and_negative_slope(self):
        rising = decode_alert_packet(alert_packet(flags=14, count=31, slope=61))
        self.assertTrue(rising.trend)
        self.assertEqual(rising.slope_c_per_min, .061)
        self.assertIn("TREND=ON", format_alert(rising))
        falling = decode_alert_packet(alert_packet(flags=12, count=31, slope=-120))
        self.assertIn("TREND=OFF", format_alert(falling))
        self.assertEqual(falling.slope_c_per_min, -.12)

    def test_fault_and_reset_never_display_off_as_safe(self):
        for flags, label in ((16, "FAULT"), (0, "AWAITING SAMPLE")):
            result = decode_alert_packet(alert_packet(flags=flags, count=0, sample=0xFFFFFFFF))
            text = format_alert(result)
            self.assertIn(label, text)
            self.assertIn("decision unavailable", text)
            self.assertNotIn("OFF", text)

    def test_history_restart_and_report_clock_wrap(self):
        result = decode_alert_packet(alert_packet(flags=40, report=10, sample=0xFFFFFFF0))
        self.assertIn("HISTORY RESTARTED", format_alert(result))

    def test_reject_bad_lengths_versions_and_reserved_bits(self):
        for data in (b"", alert_packet()[:-1], alert_packet() + b"\0",
                     alert_packet(version=2), alert_packet(flags=64)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_alert_packet(data)

    def test_reject_inconsistent_alert_states(self):
        malformed = [alert_packet(flags=24), alert_packet(count=0), alert_packet(count=32),
                     alert_packet(count=31), alert_packet(flags=12, count=1, slope=20),
                     alert_packet(flags=10), alert_packet(flags=12, count=31),
                     alert_packet(slope=0),
                     alert_packet(sample=1002, report=1001), alert_packet(threshold=-32768),
                     alert_packet(flags=0), alert_packet(flags=17, count=0, sample=0xFFFFFFFF),
                     alert_packet(flags=32, count=0, sample=0xFFFFFFFF)]
        for data in malformed:
            with self.subTest(data=data), self.assertRaises(ValueError):
                decode_alert_packet(data)

    def test_valid_timestamp_at_last_millisecond_before_wrap(self):
        result = decode_alert_packet(alert_packet(sample=0xFFFFFFFF, report=0))
        self.assertTrue(result.valid)
        self.assertEqual(result.sample_uptime_ms, 0xFFFFFFFF)


class AlertFreshnessTests(unittest.TestCase):
    def test_only_valid_decision_stales_after_fifteen_seconds(self):
        watch = AlertWatch()
        self.assertFalse(watch.warning_due(100))
        watch.observe(decode_alert_packet(alert_packet()), 100)
        self.assertFalse(watch.warning_due(114.999))
        self.assertTrue(watch.warning_due(115))
        self.assertFalse(watch.warning_due(116))
        self.assertTrue(watch.warning_due(130))

    def test_duplicate_cannot_refresh_alert(self):
        watch = AlertWatch()
        reading = decode_alert_packet(alert_packet())
        self.assertTrue(watch.observe(reading, 0))
        self.assertFalse(watch.observe(reading, 14))
        self.assertTrue(watch.warning_due(15))

    def test_fault_and_reset_do_not_repeat_stale_warning(self):
        for flags in (0, 16):
            watch = AlertWatch()
            watch.observe(decode_alert_packet(alert_packet()), 0)
            self.assertTrue(watch.warning_due(15))
            watch.observe(decode_alert_packet(alert_packet(flags=flags, count=0,
                          sample=0xFFFFFFFF, seq=2)), 16)
            self.assertFalse(watch.warning_due(100))

    def test_new_valid_event_recovers_and_clocks_can_wrap(self):
        watch = AlertWatch()
        watch.observe(decode_alert_packet(alert_packet(seq=65535)), 0)
        self.assertTrue(watch.warning_due(15))
        watch.observe(decode_alert_packet(alert_packet(seq=0, report=5, sample=0)), 16)
        self.assertFalse(watch.warning_due(30.99))
        self.assertTrue(watch.warning_due(31))


if __name__ == "__main__":
    unittest.main()
