#ifndef TEMP_BLE_TELEMETRY_PACKET_HPP
#define TEMP_BLE_TELEMETRY_PACKET_HPP

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace temp_ble {
constexpr std::size_t kPacketSize = 14;
constexpr std::uint8_t kVersion = 1;
constexpr std::uint8_t kTmpValid = 0x01;
constexpr std::uint8_t kShtValid = 0x02;
constexpr std::uint8_t kTmpWait = 0x04;
constexpr std::int16_t kInvalidTemperature = -32768;
constexpr std::uint16_t kInvalidHumidity = 65535;

inline void put16(std::uint8_t *out, std::uint16_t value) {
  out[0] = static_cast<std::uint8_t>(value);
  out[1] = static_cast<std::uint8_t>(value >> 8);
}

inline bool encodeTemperature(float value, std::int16_t &encoded) {
  if (!std::isfinite(value)) return false;
  const double rounded = std::round(static_cast<double>(value) * 100.0);
  if (rounded < -32767.0 || rounded > 32767.0) return false;
  encoded = static_cast<std::int16_t>(rounded);
  return true;
}

// All multibyte fields are explicitly little endian. No struct layout/padding.
// tmpStatus: 1=fresh reading, 0=conversion pending, -1=read/init error.
// Invalid fields use sentinels and clear validity bits; WAIT is never zero C.
inline void encodePacket(std::uint8_t *out, std::uint16_t sequence,
                         std::uint32_t uptimeMs, int tmpStatus, float tmpC,
                         bool shtValid, float ambientC, float humidityPct) {
  std::int16_t skin = kInvalidTemperature, ambient = kInvalidTemperature;
  std::uint16_t humidity = kInvalidHumidity;
  std::uint8_t flags = tmpStatus == 0 ? kTmpWait : 0;
  if (tmpStatus == 1 && tmpC >= -55 && tmpC <= 150 &&
      encodeTemperature(tmpC, skin)) flags |= kTmpValid;
  if (shtValid && ambientC >= -45 && ambientC <= 130 &&
      encodeTemperature(ambientC, ambient) &&
      std::isfinite(humidityPct) && humidityPct >= 0 && humidityPct <= 100) {
    humidity = static_cast<std::uint16_t>(
        std::round(static_cast<double>(humidityPct) * 100.0));
    flags |= kShtValid;
  } else {
    ambient = kInvalidTemperature;
  }
  out[0] = kVersion;
  out[1] = flags;
  put16(out + 2, sequence);
  put16(out + 4, static_cast<std::uint16_t>(uptimeMs));
  put16(out + 6, static_cast<std::uint16_t>(uptimeMs >> 16));
  put16(out + 8, static_cast<std::uint16_t>(skin));
  put16(out + 10, static_cast<std::uint16_t>(ambient));
  put16(out + 12, humidity);
}
}  // namespace temp_ble
#endif
