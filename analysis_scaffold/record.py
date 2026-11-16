"""Record a deterministic packet replay of the scripted demo scenario.

Runs the existing Engine offline (no server, no hardware) against the same
DemoHub validation the live server uses (format, enrollment, authentication,
consent, counter). Only packets the hub accepts are recorded, because the live
hub only hands accepted packets to analysis; the non-consenting wearer's
packets and the scripted replayed, tampered and unknown-pod packets are counted
in the header by the hub's reason, never replayed into an analyzer. The recording is the shared input for every
analysis approach, so their comparison is fair and matches the live path.

Determinism: pod noise comes from per-pod seeded RNGs inside Engine; the only
unseeded randomness is the air-temperature drift, seeded here.
"""
import json
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from body_heat_demo.engine import build_demo
from f1_sideline_demo.hub import Rejected

SESSION_LENGTH_S = 200          # covers the full scripted timeline (last event t=170)
OUT_PATH = os.path.join(os.path.dirname(__file__), "recordings", "session_default.jsonl")


def record_session(seconds=SESSION_LENGTH_S):
    """Return (header, packets): every hub-accepted packet as (t, packet), in
    the order the hub accepted them."""
    _, hub, _, _, eng = build_demo(autoplay=True, seed_history=False, analyzer="relay")
    accepted, rejected = [], Counter()
    validate = hub.accept

    def capture(pkt, t):
        copy = dict(pkt) if isinstance(pkt, dict) else pkt
        try:
            validate(pkt, t)
        except Rejected as e:
            rejected[str(e)] += 1
            raise
        accepted.append((t, copy))
        return True

    hub.accept = capture        # Engine calls hub.accept for broadcasts and injected packets
    state = random.getstate()
    try:
        random.seed(0)          # air-temperature drift only; pod RNGs are already seeded
        for _ in range(seconds):
            eng.step()
    finally:
        random.setstate(state)
    header = {
        "type": "header",
        "session_id": "replay-default",
        "source": "body_heat_demo synthetic scripted scenario (no hardware)",
        "validation": "hub-accepted packets only (DemoHub ingest rules, same as the live server)",
        "rejected_not_recorded": dict(sorted(rejected.items())),   # hub reason -> packet count
        "scripted_events": [
            {"t": t, "action": action, "jersey": jersey}
            for (t, action, jersey) in eng.scripted
        ],
    }
    return header, accepted


def main():
    header, packets = record_session()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        f.write(json.dumps(header) + "\n")
        for t, pkt in packets:
            f.write(json.dumps({"type": "packet", "t": t, "packet": pkt}) + "\n")
    print(f"wrote {len(packets)} hub-accepted packets to {OUT_PATH} "
          f"({sum(header['rejected_not_recorded'].values())} rejected by the hub, not recorded)")


if __name__ == "__main__":
    main()
