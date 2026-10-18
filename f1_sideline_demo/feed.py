"""Synthetic pod feed for the F1 demo. Stands in for Subsystem C: each
synthetic pod measures every MEASURE_EVERY_S seconds, broadcasts every second,
and decides its own alert from the team's trend and absolute settings, exactly
like the real pod will. Names never appear in packets, only device IDs."""

MEASURE_EVERY_S = 10
TREND_LIMIT_C_PER_MIN = 0.5   # team setting: skin trend limit
ABSOLUTE_LIMIT_C = 38.5       # team setting: skin absolute limit


class SyntheticPod:
    def __init__(self, device_id, seed, base_skin_c=35.8, air_c=22.0, humidity_pct=45.0):
        import random
        self.device_id = device_id
        self.rng = random.Random(seed)
        self.base_skin_c = base_skin_c
        self.air_c = air_c
        self.humidity_pct = humidity_pct
        self.counter = 0
        self.battery_pct = 100.0
        self.silent = False            # simulate a pod that stops broadcasting
        self._forced_rise = False      # scenario: skin temperature climbs
        self._samples = []             # (t, skin_c) for the trend decision

    def start_scenario_rise(self):
        self._forced_rise = True

    def go_silent(self):
        self.silent = True

    def _skin_c(self, t):
        drift = 0.0
        if self._forced_rise and t >= 10:
            # Climbs ~5 C/min at first, then plateaus +1.2 C (athlete rests).
            drift = min(1.2, 0.09 * (t - 10))
        return self.base_skin_c + drift + self.rng.uniform(-0.03, 0.03)

    def _decide_alert(self):
        window = self._samples[-6:]  # trend over about the last minute
        if len(window) < 3:
            return None
        latest = window[-1][1]
        if latest >= ABSOLUTE_LIMIT_C:
            return "absolute"
        span_s = window[-1][0] - window[0][0]
        if span_s >= 2 * MEASURE_EVERY_S:
            rate_c_per_min = (latest - window[0][1]) / (span_s / 60.0)
            if rate_c_per_min >= TREND_LIMIT_C_PER_MIN:
                return "trend"
        return None

    def broadcast(self, t):
        """One 1 Hz broadcast, or None if the pod is silent."""
        if self.silent:
            return None
        self.counter += 1
        self.battery_pct = max(0.0, self.battery_pct - 0.002)
        if t % MEASURE_EVERY_S == 0:
            skin = self._skin_c(t)
            self._samples.append((t, skin))
            self._last_measurement = {
                "measurement_id": f"{self.device_id}-m{t}",
                "skin_c": round(skin, 2),
                "air_c": round(self.air_c + self.rng.uniform(-0.2, 0.2), 2),
                "humidity_pct": round(self.humidity_pct + self.rng.uniform(-1, 1), 1),
                "motion": round(self.rng.uniform(0.0, 1.0), 2),
                "battery_pct": round(self.battery_pct, 1),
            }
        packet = dict(self._last_measurement)
        packet.update({
            "device_id": self.device_id,
            "counter": self.counter,
            "alert": self._decide_alert(),
            "skin_contact": True,
        })
        return packet


def demo_roster():
    """Six synthetic wearers for the December-prototype demo. Names live only
    here in the display roster, never in packets."""
    return [
        {"jersey": 3,  "name": "A. Rivera",  "device_id": "pod-03"},
        {"jersey": 7,  "name": "B. Osei",    "device_id": "pod-07"},
        {"jersey": 11, "name": "C. Novak",   "device_id": "pod-11"},
        {"jersey": 18, "name": "D. Kim",     "device_id": "pod-18"},
        {"jersey": 24, "name": "E. Santos",  "device_id": "pod-24"},
        {"jersey": 30, "name": "F. Meyer",   "device_id": "pod-30"},
    ]


def demo_pods():
    pods = {}
    for i, member in enumerate(demo_roster()):
        pods[member["device_id"]] = SyntheticPod(member["device_id"], seed=100 + i)
    return pods
