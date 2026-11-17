#include "../XIAO_Temperature_BLE/telemetry_packet.hpp"

#include <array>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>

using Packet = std::array<std::uint8_t, 14>;
static unsigned checks = 0;

static void require(bool condition, const char *name) {
  ++checks;
  if (!condition) {
    std::cerr << "FAIL: " << name << '\n';
    std::exit(1);
  }
}

static void expectPacket(const Packet &actual, const Packet &expected,
                         const char *name) {
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

static Packet packet(std::uint16_t seq, std::uint32_t ms, int status,
                     float skin, bool sht, float ambient, float rh) {
  Packet result{};
  temp_ble::encodePacket(result.data(), seq, ms, status, skin, sht, ambient, rh);
  return result;
}

int main() {
  static_assert(temp_ble::kPacketSize == 14, "Wire protocol must remain 14 bytes");
  const float nan = std::numeric_limits<float>::quiet_NaN();
  const float inf = std::numeric_limits<float>::infinity();

  // Golden bytes independently calculated from Python struct format <BBHIhhH.
  // 30.92 C=3092, 23.50 C=2350, 45.67 %=4567; nontrivial endian fields.
  expectPacket(packet(0x1234, 0x78563412, 1, 30.92f, true, 23.5f, 45.67f),
               {0x01,0x03,0x34,0x12,0x12,0x34,0x56,0x78,
                0x14,0x0c,0x2e,0x09,0xd7,0x11}, "golden normal reading");

  expectPacket(packet(1, 0xfedcba98, 1, -12.5f, true, -1.0f, 100.0f),
               {0x01,0x03,0x01,0x00,0x98,0xba,0xdc,0xfe,
                0x1e,0xfb,0x9c,0xff,0x10,0x27}, "negative two's complement");

  // .125 is exactly representable: exercise ties away from zero explicitly.
  expectPacket(packet(0, 0, 1, -0.125f, true, 0.125f, 50.125f),
               {0x01,0x03,0x00,0x00,0x00,0x00,0x00,0x00,
                0xf3,0xff,0x0d,0x00,0x95,0x13}, "rounding positive/negative ties");

  expectPacket(packet(0, 0, 0, 99.0f, true, 25.0f, 50.0f),
               {0x01,0x06,0x00,0x00,0x00,0x00,0x00,0x00,
                0x00,0x80,0xc4,0x09,0x88,0x13}, "WAIT ignores stale TMP value");

  const Packet invalid{0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
                       0x00,0x80,0x00,0x80,0xff,0xff};
  expectPacket(packet(0, 0, -1, 99.0f, false, 25.0f, 50.0f), invalid,
               "errors ignore supplied values and use sentinels");
  expectPacket(packet(0, 0, 2, 99.0f, false, 25.0f, 50.0f), invalid,
               "unknown TMP status is invalid");
  Packet pending = invalid;
  pending[1] = 0x04;
  expectPacket(packet(0, 0, 0, nan, false, nan, nan), pending,
               "WAIT stays distinguishable from read error");

  const Packet onlyTmp{0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x00,
                       0xc4,0x09,0x00,0x80,0xff,0xff};
  for (float value : {nan, inf, -inf, 400.0f, -400.0f, -327.68f}) {
    expectPacket(packet(0, 0, 1, value, false, 25.0f, 50.0f), invalid,
                 "invalid/nonfinite TMP cannot become numeric reading");
    expectPacket(packet(0, 0, 1, 25.0f, true, value, 50.0f), onlyTmp,
                 "invalid ambient also invalidates humidity pair");
  }
  for (float value : {nan, inf, -inf, -0.01f, 100.01f}) {
    expectPacket(packet(0, 0, 1, 25.0f, true, 23.0f, value), onlyTmp,
                 "invalid humidity also invalidates ambient pair");
  }

  expectPacket(packet(0, 0, 1, 0.0f, true, 0.0f, 0.0f),
               {0x01,0x03,0x00,0x00,0x00,0x00,0x00,0x00,
                0x00,0x00,0x00,0x00,0x00,0x00}, "valid zero is not missing");

  expectPacket(packet(0, 0, 1, -55.0f, true, -45.0f, 0.0f),
               {0x01,0x03,0x00,0x00,0x00,0x00,0x00,0x00,
                0x84,0xea,0x6c,0xee,0x00,0x00}, "accepted lower bounds");
  expectPacket(packet(0, 0, 1, 150.0f, true, 130.0f, 100.0f),
               {0x01,0x03,0x00,0x00,0x00,0x00,0x00,0x00,
                0x98,0x3a,0xc8,0x32,0x10,0x27}, "accepted upper bounds");
  for (float value : {-55.01f, 150.01f})
    expectPacket(packet(0, 0, 1, value, false, nan, nan), invalid,
                 "TMP rejects just-outside bounds before rounding");
  for (float value : {-45.01f, 130.01f})
    expectPacket(packet(0, 0, 1, 25.0f, true, value, 50.0f), onlyTmp,
                 "ambient rejects just-outside bounds before rounding");

  std::uint16_t sequence = 65535;
  Packet beforeWrap = packet(sequence++, 0xffffffff, -1, nan, false, nan, nan);
  Packet afterWrap = packet(sequence++, 0, -1, nan, false, nan, nan);
  expectPacket(beforeWrap,
               {0x01,0x00,0xff,0xff,0xff,0xff,0xff,0xff,
                0x00,0x80,0x00,0x80,0xff,0xff}, "sequence/uptime maxima");
  expectPacket(afterWrap, invalid, "sequence and uptime wrap to zero");
  require(sequence == 1, "16-bit sequence increment wraps");

  std::array<std::uint8_t, 16> guarded;
  guarded.fill(0xa5);
  temp_ble::encodePacket(guarded.data()+1, 0, 0, -1, nan, false, nan, nan);
  require(guarded.front() == 0xa5 && guarded.back() == 0xa5,
          "encoder writes exactly the 14-byte output region");
  for (std::size_t i = 0; i < invalid.size(); ++i)
    require(guarded[i+1] == invalid[i], "all bytes overwritten on invalid packet");

  std::cout << "PASS: " << checks << " packet protocol checks\n";
}
