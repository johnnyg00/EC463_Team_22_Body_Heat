#ifndef TEMP_ALERT_SAMPLER_HPP
#define TEMP_ALERT_SAMPLER_HPP

#include "alert_module.hpp"
#include <cstdint>
#include <limits>

namespace temp_alert {
constexpr std::uint64_t kMaxSourceAgeMs = 1500;
constexpr std::uint64_t kUnknownAge = std::numeric_limits<std::uint64_t>::max();

struct Event {
  bool emitted = false;
  bool valid = false;
  bool fault = false;
  std::uint64_t report_ms = 0;
  std::uint64_t sample_ms = 0;
  std::uint64_t source_age_ms = kUnknownAge;
  body_heat::Decision decision;
};

// Samples a continuous sensor's latest successfully acquired value. Sampling
// time is the actual software snapshot time; source age is separately recorded.
class AlertSampler {
 public:
  explicit AlertSampler(double absoluteLimit = 29.0)
      : core_(makeConfig(absoluteLimit)), limit_(absoluteLimit) {}

  // WAIT preserves a recent cache. Any hard error discards it immediately;
  // successful sensor reinitialization by itself must not call observe(1,...).
  void observe(int tmpStatus, double temperatureC, std::uint64_t sourceMs) {
    if (tmpStatus == 0) return;
    if (tmpStatus == 1 && std::isfinite(temperatureC) &&
        temperatureC >= -55.0 && temperatureC <= 150.0) {
      source_c_ = temperatureC;
      source_ms_ = sourceMs;
      have_source_ = true;
      hard_fault_ = false;
    } else {
      have_source_ = false;
      hard_fault_ = true;
    }
  }

  Event poll(std::uint64_t nowMs) {
    if (have_clock_ && nowMs < last_poll_ms_) {
      core_.reset();
      have_source_ = false;
      hard_fault_ = true;
      scheduled_ = false;
      last_poll_ms_ = nowMs;
      return invalidate(nowMs, kUnknownAge);
    }
    have_clock_ = true;
    last_poll_ms_ = nowMs;
    const std::uint64_t nowSec = nowMs / 1000;
    const bool due = !scheduled_ || nowSec >= next_due_sec_;
    const bool future = have_source_ && source_ms_ > nowMs;
    const std::uint64_t age = have_source_ && !future
                                  ? nowMs - source_ms_ : kUnknownAge;
    if (future) {
      have_source_ = false;
      hard_fault_ = true;
    }
    const bool fresh = have_source_ && age <= kMaxSourceAgeMs && !hard_fault_;
    if (!core_.configuration_valid() || !fresh) {
      if (scheduled_ && due) next_due_sec_ = nowSec + 10;
      // Before the first reading or after an intentional reset, WAIT is not
      // a sensor fault. Hard failure and an expired established cache are.
      if (!core_.configuration_valid() || hard_fault_ || have_source_ || state_.valid)
        return invalidate(nowMs, age);
      return Event();
    }
    if (!due) return Event();

    // No catch-up or invented timestamps after a delayed loop/missing sample.
    // Reanchor after actual time so subsecond work cannot accumulate drift.
    scheduled_ = true;
    next_due_sec_ = nowSec + 10;
    const body_heat::Decision decision = core_.update(source_c_, nowSec);
    if (!decision.input_accepted) return invalidate(nowMs, age);
    state_ = Event();
    state_.emitted = state_.valid = true;
    state_.report_ms = state_.sample_ms = nowMs;
    state_.source_age_ms = age;
    state_.decision = decision;
    return state_;
  }

  Event reset(std::uint64_t nowMs) {
    core_.reset();
    have_source_ = hard_fault_ = scheduled_ = false;
    have_clock_ = true;
    last_poll_ms_ = nowMs;
    state_ = Event();
    state_.emitted = true;
    state_.report_ms = nowMs;
    return state_;
  }

  const Event &state() const { return state_; }
  double absolute_limit_c() const { return limit_; }

 private:
  static body_heat::Config makeConfig(double limit) {
    body_heat::Config config;
    config.absolute_limit_c = limit;
    return config;
  }
  Event invalidate(std::uint64_t nowMs, std::uint64_t age) {
    if (!state_.valid && state_.fault) return Event();
    state_ = Event();
    state_.emitted = state_.fault = true;
    state_.report_ms = nowMs;
    state_.source_age_ms = age;
    return state_;
  }

  body_heat::AlertModule core_;
  double limit_;
  Event state_;
  bool have_source_ = false, hard_fault_ = false, scheduled_ = false;
  bool have_clock_ = false;
  double source_c_ = 0;
  std::uint64_t source_ms_ = 0, next_due_sec_ = 0, last_poll_ms_ = 0;
};
}  // namespace temp_alert
#endif
