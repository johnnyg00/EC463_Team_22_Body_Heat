#ifndef TEMP_BLE_ALERT_PACKET_HPP
#define TEMP_BLE_ALERT_PACKET_HPP

#include "alert_sampler.hpp"
#include "telemetry_packet.hpp"
#include <limits>

namespace temp_ble {
constexpr std::size_t kAlertPacketSize = 19;
constexpr std::int32_t kInvalidSlope = std::numeric_limits<std::int32_t>::min();

inline void put32(std::uint8_t *out, std::uint32_t value) {
  put16(out, static_cast<std::uint16_t>(value));
  put16(out + 2, static_cast<std::uint16_t>(value >> 16));
}

// '<BBHIIhiB': version, flags, event sequence, report ms, sample ms,
// absolute threshold *100, slope C/min *1000, accepted sample count.
inline void encodeAlertPacket(std::uint8_t *out, std::uint16_t eventSequence,
                             std::uint32_t reportMs, const temp_alert::Event &event,
                             double absoluteLimit) {
  const bool valid = event.valid && !event.fault && event.decision.input_accepted;
  const bool ready = valid && event.decision.trend_ready &&
                     std::isfinite(event.decision.slope_c_per_min);
  std::uint8_t flags = event.fault ? 0x10 : 0;
  if (valid) {
    flags |= 0x08;
    if (event.decision.absolute_alert) flags |= 0x01;
    if (ready && event.decision.trend_alert) flags |= 0x02;
    if (ready) flags |= 0x04;
    if (event.decision.history_restarted) flags |= 0x20;
  }
  std::int16_t limit = kInvalidTemperature;
  if (std::isfinite(absoluteLimit)) {
    const double rounded = std::round(absoluteLimit * 100.0);
    if (rounded >= -32767.0 && rounded <= 32767.0)
      limit = static_cast<std::int16_t>(rounded);
  }
  std::int32_t slope = kInvalidSlope;
  if (ready) {
    const double rounded = std::round(event.decision.slope_c_per_min * 1000.0);
    if (rounded >= -2147483647.0 && rounded <= 2147483647.0)
      slope = static_cast<std::int32_t>(rounded);
    else {
      flags &= static_cast<std::uint8_t>(~0x06); // Unrepresentable: not ready.
    }
  }
  out[0] = 1;
  out[1] = flags;
  put16(out + 2, eventSequence);
  put32(out + 4, reportMs);
  put32(out + 8, valid ? static_cast<std::uint32_t>(event.sample_ms) : 0xFFFFFFFFu);
  put16(out + 12, static_cast<std::uint16_t>(limit));
  put32(out + 14, static_cast<std::uint32_t>(slope));
  out[18] = valid ? static_cast<std::uint8_t>(
      event.decision.sample_count > 255 ? 255 : event.decision.sample_count) : 0;
}
}  // namespace temp_ble
#endif
