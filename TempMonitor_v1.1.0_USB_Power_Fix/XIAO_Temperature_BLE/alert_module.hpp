#ifndef BODY_HEAT_ALERT_MODULE_HPP
#define BODY_HEAT_ALERT_MODULE_HPP

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace body_heat {

struct Config {
    std::uint32_t window_seconds = 300;
    std::uint32_t sample_interval_seconds = 10;
    double slope_limit_c_per_min = 0.06;
    double absolute_limit_c = 38.0;
};

struct Decision {
    bool input_accepted = false;
    bool history_restarted = false;
    bool trend_ready = false;
    bool trend_alert = false;
    bool absolute_alert = false;
    double slope_c_per_min = std::numeric_limits<double>::quiet_NaN();
    std::size_t sample_count = 0;
};

// Hardware-independent decision logic. The caller supplies simulated or sensed
// temperatures and monotonically increasing timestamps in whole seconds.
// Storage is fixed; this class performs no allocation, I/O, or waiting.
class AlertModule {
public:
    explicit AlertModule(Config config = Config())
        : config_(config), required_samples_(0), valid_(false), samples_(),
          head_(0), count_(0), have_timestamp_(false), last_timestamp_(0), last_() {
        valid_ = validate_config();
        if (valid_) {
            required_samples_ = config_.window_seconds /
                                    config_.sample_interval_seconds + 1U;
        }
    }

    Config config() const { return config_; }
    bool configuration_valid() const { return valid_; }

    void reset() {
        head_ = 0;
        count_ = 0;
        have_timestamp_ = false;
        last_timestamp_ = 0;
        last_ = Decision();
    }

    // A rejected sample does not change state. Its returned flags describe the
    // last accepted sample, so callers must check input_accepted before using it.
    // Any positive cadence mismatch starts a new trend window, while the
    // current temperature still receives an immediate absolute-threshold check.
    Decision update(double temperature_c, std::uint64_t timestamp_seconds) {
        if (!valid_ || !std::isfinite(temperature_c) ||
            (have_timestamp_ && timestamp_seconds <= last_timestamp_)) {
            Decision rejected = last_;
            rejected.input_accepted = false;
            rejected.history_restarted = false;
            return rejected;
        }

        bool restarted = false;
        if (have_timestamp_ &&
            timestamp_seconds - last_timestamp_ != config_.sample_interval_seconds) {
            head_ = 0;
            count_ = 0;
            restarted = true;
        }

        if (count_ == required_samples_) {
            head_ = (head_ + 1U) % kCapacity;
            --count_;
        }
        Sample& next = samples_[(head_ + count_) % kCapacity];
        next.timestamp_seconds = timestamp_seconds;
        next.temperature_c = temperature_c;
        ++count_;
        have_timestamp_ = true;
        last_timestamp_ = timestamp_seconds;

        Decision result;
        result.input_accepted = true;
        result.history_restarted = restarted;
        result.absolute_alert = temperature_c > config_.absolute_limit_c;
        result.sample_count = count_;
        if (count_ == required_samples_) {
            result.slope_c_per_min = fitted_slope_c_per_min();
            result.trend_ready = std::isfinite(result.slope_c_per_min);
            // This tiny allowance handles floating-point rounding at equality
            // (e.g. the 36.0 -> 36.3 C boundary test). It is a numerical
            // tolerance in C/min, not physical hysteresis or a sensor tolerance.
            const double slope_roundoff_c_per_min = 1e-9;
            result.trend_alert = result.trend_ready &&
                result.slope_c_per_min > config_.slope_limit_c_per_min &&
                result.slope_c_per_min - config_.slope_limit_c_per_min >
                    slope_roundoff_c_per_min;
        }
        last_ = result;
        return result;
    }

private:
    enum { kCapacity = 64 };
    struct Sample {
        std::uint64_t timestamp_seconds;
        double temperature_c;
    };

    bool validate_config() const {
        if (config_.window_seconds == 0 || config_.sample_interval_seconds == 0 ||
            !std::isfinite(config_.slope_limit_c_per_min) ||
            config_.slope_limit_c_per_min <= 0.0 ||
            !std::isfinite(config_.absolute_limit_c)) {
            return false;
        }
        return config_.window_seconds % config_.sample_interval_seconds == 0 &&
               config_.window_seconds / config_.sample_interval_seconds < kCapacity;
    }

    double fitted_slope_c_per_min() const {
        // Subtract the integer timestamp origin before converting to double.
        // Centering both coordinates avoids cancellation from a large clock
        // origin or the approximately 37 C offset in temperature measurements.
        const Sample& first = samples_[head_];
        double mean_x = 0.0;
        double mean_y = 0.0;
        for (std::size_t i = 0; i < count_; ++i) {
            const Sample& sample = samples_[(head_ + i) % kCapacity];
            mean_x += static_cast<double>(sample.timestamp_seconds -
                                          first.timestamp_seconds);
            mean_y += sample.temperature_c - first.temperature_c;
        }
        mean_x /= static_cast<double>(count_);
        mean_y /= static_cast<double>(count_);

        double covariance = 0.0;
        double time_variance = 0.0;
        for (std::size_t i = 0; i < count_; ++i) {
            const Sample& sample = samples_[(head_ + i) % kCapacity];
            const double x = static_cast<double>(sample.timestamp_seconds -
                                                first.timestamp_seconds) - mean_x;
            const double y = (sample.temperature_c - first.temperature_c) - mean_y;
            covariance += x * y;
            time_variance += x * x;
        }
        if (time_variance <= 0.0) {
            return std::numeric_limits<double>::quiet_NaN();
        }
        return (covariance / time_variance) * 60.0;
    }

    Config config_;
    std::size_t required_samples_;
    bool valid_;
    std::array<Sample, kCapacity> samples_;
    std::size_t head_;
    std::size_t count_;
    bool have_timestamp_;
    std::uint64_t last_timestamp_;
    Decision last_;
};

}  // namespace body_heat

#endif  // BODY_HEAT_ALERT_MODULE_HPP
