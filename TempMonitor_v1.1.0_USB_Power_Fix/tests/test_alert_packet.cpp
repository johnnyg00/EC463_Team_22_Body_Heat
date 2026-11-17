#include "../XIAO_Temperature_BLE/alert_packet.hpp"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>

namespace {
using Packet = std::array<std::uint8_t, 19>;
unsigned checks = 0;
void require(bool condition, const char *name) {
  ++checks;
  if (!condition) {
    std::cerr << "FAIL: " << name << '\n';
    std::exit(1);
  }
}
void expect(const Packet &actual, const Packet &expected, const char *name) {
  ++checks;
  if (actual == expected) return;
  std::cerr << "FAIL: " << name << "\nactual:   ";
  for (auto b : actual) std::cerr << std::hex << std::setw(2)
                                 << std::setfill('0') << unsigned(b) << ' ';
  std::cerr << "\nexpected: ";
  for (auto b : expected) std::cerr << std::hex << std::setw(2)
                                   << std::setfill('0') << unsigned(b) << ' ';
  std::cerr << '\n';
  std::exit(1);
}
Packet packet(const temp_alert::Event &event, double threshold = 29.0,
              std::uint16_t seq = 0, std::uint32_t reportMs = 0) {
  Packet out{};
  temp_ble::encodeAlertPacket(out.data(), seq, reportMs, event, threshold);
  return out;
}
std::uint32_t u32(const Packet &p, unsigned offset) {
  return std::uint32_t(p[offset]) | (std::uint32_t(p[offset + 1]) << 8) |
         (std::uint32_t(p[offset + 2]) << 16) |
         (std::uint32_t(p[offset + 3]) << 24);
}
temp_alert::Event validEvent() {
  temp_alert::Event e;
  e.emitted = e.valid = e.decision.input_accepted = true;
  e.decision.trend_ready = e.decision.trend_alert = true;
  e.decision.absolute_alert = true;
  e.decision.slope_c_per_min = 0.1;
  e.decision.sample_count = 31;
  return e;
}
} // namespace

int main() {
  static_assert(temp_ble::kAlertPacketSize == 19,
                "Alert protocol must remain 19 bytes (<BBHIIhiB)");
  const Packet invalid{0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
                       0xff,0xff,0xff,0xff,0x54,0x0b,
                       0x00,0x00,0x00,0x80,0x00};
  auto event = validEvent();
  event.sample_ms = 0xfedcba98u;
  // Golden bytes: flags absolute+trend+ready+valid; 29C=2900; 0.1C/min=100.
  expect(packet(event, 29.0, 0x1234, 0x78563412),
         {0x01,0x0f,0x34,0x12,0x12,0x34,0x56,0x78,
          0x98,0xba,0xdc,0xfe,0x54,0x0b,0x64,0x00,0x00,0x00,0x1f},
         "golden normal alert, explicit little endian fields");

  event.sample_ms = 0;
  event.decision.absolute_alert = event.decision.trend_alert = false;
  event.decision.slope_c_per_min = -0.125;
  expect(packet(event, 29.125),
         {0x01,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,
          0x00,0x00,0x00,0x00,0x61,0x0b,0x83,0xff,0xff,0xff,0x1f},
         "negative slope and threshold tie round away from zero");
  event.decision.slope_c_per_min = 0.0625;
  expect(packet(event, -29.125),
         {0x01,0x0c,0x00,0x00,0x00,0x00,0x00,0x00,
          0x00,0x00,0x00,0x00,0x9f,0xf4,0x3f,0x00,0x00,0x00,0x1f},
         "positive slope tie and negative threshold tie");
  event.decision.slope_c_per_min = -0.0625;
  require(u32(packet(event), 14) == 0xffffffc1u, "negative slope tie is -63");

  expect(packet(temp_alert::Event()), invalid, "startup/reset unavailable packet");
  event = validEvent();
  event.valid = false;
  expect(packet(event), invalid, "invalid event masks stale decision flags");
  event = validEvent();
  event.decision.input_accepted = false;
  expect(packet(event), invalid, "rejected input masks every old decision field");
  event = validEvent();
  event.fault = true;
  Packet fault = invalid;
  fault[1] = 0x10;
  expect(packet(event), fault, "fault takes priority over inconsistent valid flag");

  event = validEvent();
  event.decision.trend_ready = false;
  event.decision.sample_count = 1;
  expect(packet(event),
         {0x01,0x09,0x00,0x00,0x00,0x00,0x00,0x00,
          0x00,0x00,0x00,0x00,0x54,0x0b,0x00,0x00,0x00,0x80,0x01},
         "absolute works during warmup, trend and slope stay unavailable");
  event.decision.history_restarted = true;
  require(packet(event)[1] == 0x29, "history restart bit accompanies accepted sample");
  event.valid = false;
  expect(packet(event), invalid, "invalid event cannot advertise history restart");

  const double nan = std::numeric_limits<double>::quiet_NaN();
  const double inf = std::numeric_limits<double>::infinity();
  for (double slope : {nan, inf, -inf, 2147483.648, -2147483.648, 1e100}) {
    event = validEvent();
    event.decision.slope_c_per_min = slope;
    const Packet out = packet(event);
    require(out[1] == 0x09, "invalid/unrepresentable slope clears ready and trend bits");
    require(u32(out, 14) == 0x80000000u, "unavailable slope uses INT32_MIN sentinel");
    require(out[18] == 31, "bad slope does not erase a valid accepted sample count");
  }
  for (double threshold : {nan, inf, -inf, 327.68, -327.68, 1e100}) {
    event = validEvent();
    const Packet out = packet(event, threshold);
    require(out[12] == 0 && out[13] == 0x80,
            "invalid threshold uses reserved INT16_MIN");
  }
  event = validEvent();
  event.decision.slope_c_per_min = 0;
  const Packet zero = packet(event, 0);
  require(zero[12] == 0 && zero[13] == 0 && u32(zero, 14) == 0,
          "zero threshold and zero slope are representable, not missing");

  event = validEvent();
  event.sample_ms = (std::uint64_t(1) << 32) + 0x12345678;
  event.decision.sample_count = 300;
  expect(packet(event, 29.0, 0xffff, 0xffffffff),
         {0x01,0x0f,0xff,0xff,0xff,0xff,0xff,0xff,
          0x78,0x56,0x34,0x12,0x54,0x0b,0x64,0x00,0x00,0x00,0xff},
         "wire time wraps to low32 bits and count saturates");
  std::uint16_t sequence = 0xffff;
  (void)packet(event, 29.0, sequence++, 0xffffffff);
  require(packet(event, 29.0, sequence++, 0)[2] == 0 && sequence == 1,
          "16-bit event sequence wraps independently");

  // Across flag combinations, unavailable decisions must never resemble safe
  // valid measurements, and trend requires valid, accepted, ready input.
  for (unsigned mask = 0; mask < 128; ++mask) {
    event = validEvent();
    event.valid = (mask & 1) != 0;
    event.fault = (mask & 2) != 0;
    event.decision.input_accepted = (mask & 4) != 0;
    event.decision.trend_ready = (mask & 8) != 0;
    event.decision.absolute_alert = (mask & 16) != 0;
    event.decision.trend_alert = (mask & 32) != 0;
    event.decision.history_restarted = (mask & 64) != 0;
    const Packet out = packet(event);
    const bool valid = event.valid && !event.fault && event.decision.input_accepted;
    require(bool(out[1] & 0x08) == valid, "valid bit requires an accepted nonfault event");
    require(bool(out[1] & 0x10) == event.fault, "fault bit always preserved");
    require(!(out[1] & 0x02) || ((out[1] & 0x0c) == 0x0c),
            "trend bit always implies ready and valid");
    require((out[1] & 0xc0) == 0, "reserved bits remain zero");
    if (!valid) {
      require((out[1] & 0x2f) == 0, "invalid event suppresses every decision flag");
      require(u32(out, 8) == 0xffffffffu && u32(out, 14) == 0x80000000u,
              "invalid event uses sample/slope sentinels");
      require(out[18] == 0, "invalid event count is zero");
    }
  }

  std::array<std::uint8_t, 21> guarded;
  guarded.fill(0xa5);
  temp_ble::encodeAlertPacket(guarded.data() + 1, 0, 0, temp_alert::Event(), 29.0);
  require(guarded.front() == 0xa5 && guarded.back() == 0xa5,
          "encoder writes exactly the 19-byte packet region");
  for (std::size_t i = 0; i < invalid.size(); ++i)
    require(guarded[i + 1] == invalid[i], "invalid encoding overwrites every byte");
  std::cout << "PASS: " << checks << " alert packet checks\n";
}
