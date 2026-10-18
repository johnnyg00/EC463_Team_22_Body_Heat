"""Minimal hub-side validation and live state (mirrors the Subsystem E rules
the views rely on): a reading is accepted only from an enrolled device with
consent and a fresh transmission counter. Repeated measurements do not create
new readings. A pod with no accepted packet for NO_SIGNAL_AFTER_S seconds is
shown as no-signal."""

NO_SIGNAL_AFTER_S = 60


class Rejected(Exception):
    """A packet failed validation and does not change state."""


class PodState:
    def __init__(self, device_id):
        self.device_id = device_id
        self.last_counter = -1
        self.last_measurement_id = None
        self.last_accepted_at = None
        self.reading = None  # dict: skin_c, air_c, humidity_pct, motion, battery_pct
        self.alert = None    # None, "trend" or "absolute" (decided on the pod)
        self.alert_since = None  # t when the current alert was raised
        self.skin_contact = True


class Hub:
    def __init__(self):
        self._enrolled = {}   # device_id -> consented (bool)
        self._pods = {}       # device_id -> PodState
        self.events = []      # (t, kind, detail), e.g. acknowledgements

    def enroll(self, device_id, consented=True):
        self._enrolled[device_id] = consented
        self._pods[device_id] = PodState(device_id)

    def set_consent(self, device_id, consented, t):
        self._enrolled[device_id] = consented
        self.events.append((t, "consent", f"{device_id} consent={consented}"))

    def accept(self, packet, t):
        """Validate one incoming packet. Returns True if accepted.

        packet keys: device_id, counter, measurement_id, skin_c, air_c,
        humidity_pct, motion, battery_pct, alert, skin_contact
        """
        dev = packet.get("device_id")
        if dev not in self._enrolled:
            raise Rejected(f"unknown device {dev}")
        if not self._enrolled[dev]:
            raise Rejected(f"no consent for {dev}")
        counter = packet.get("counter", -1)
        state = self._pods[dev]
        if counter <= state.last_counter:
            raise Rejected(f"replayed counter {counter}")
        state.last_counter = counter
        state.last_accepted_at = t
        new_alert = packet.get("alert")
        if new_alert != state.alert:
            state.alert_since = t if new_alert else None
        state.alert = new_alert
        state.skin_contact = bool(packet.get("skin_contact", True))
        if packet.get("measurement_id") != state.last_measurement_id:
            # New measurement (pods measure every 10 s, broadcast every 1 s).
            state.last_measurement_id = packet.get("measurement_id")
            state.reading = {
                "skin_c": packet["skin_c"],
                "air_c": packet["air_c"],
                "humidity_pct": packet["humidity_pct"],
                "motion": packet["motion"],
                "battery_pct": packet["battery_pct"],
            }
        return True

    def reading_age_s(self, device_id, t):
        state = self._pods[device_id]
        if state.last_accepted_at is None:
            return None
        return t - state.last_accepted_at

    def no_signal(self, device_id, t):
        age = self.reading_age_s(device_id, t)
        return age is None or age >= NO_SIGNAL_AFTER_S

    def acknowledge(self, device_id, user, t):
        """Staff acknowledgement: recorded with time and user. It never clears
        the alert; only the pod's own rules can do that."""
        self.events.append((t, "ack", f"{user} acknowledged {device_id}"))

    def state(self, device_id):
        return self._pods[device_id]
