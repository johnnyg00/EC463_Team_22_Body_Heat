"""Terminal demo for the Shark Tank pitch: a 75-second slice of practice with
six synthetic wearers. One wearer's skin temperature climbs and crosses the
team's trend setting (pod raises the alert), staff acknowledge it, the pod
clears it, and one pod goes silent long enough to show the no-signal state.

Run:  python -m f1_sideline_demo.demo            (fast, prints key moments)
      python -m f1_sideline_demo.demo --realtime (1 update per second)
      python -m f1_sideline_demo.demo --athlete pod-11   (athlete view sample)
"""

import sys
import time

from .feed import demo_pods, demo_roster
from .hub import Hub, Rejected
from .views import (AccessDenied, get_athlete_view, render_staff_screen)

DEMO_SECONDS = 75
RISE_DEVICE = "pod-11"     # jersey 11 climbs over the trend setting
SILENT_DEVICE = "pod-24"   # jersey 24 stops broadcasting at t=10
SILENCE_AT_S = 10
ACK_AT_S = 32


def run(realtime=False, show_every=5):
    roster = demo_roster()
    pods = demo_pods()
    hub = Hub()
    for m in roster:
        hub.enroll(m["device_id"], consented=True)

    pods[RISE_DEVICE].start_scenario_rise()
    acknowledged = set()
    prev_alert = None

    for t in range(DEMO_SECONDS + 1):
        if t == SILENCE_AT_S:
            pods[SILENT_DEVICE].go_silent()
        for dev, pod in pods.items():
            packet = pod.broadcast(t)
            if packet is None:
                continue
            try:
                hub.accept(packet, t)
            except Rejected:
                pass  # demo pods are well-behaved; real pods may not be
        if t == ACK_AT_S:
            hub.acknowledge(RISE_DEVICE, user="trainer", t=t)
            acknowledged.add(RISE_DEVICE)

        alert_now = hub.state(RISE_DEVICE).alert
        alert_changed = alert_now != prev_alert
        prev_alert = alert_now
        interesting = (alert_changed or t in (0, ACK_AT_S)
                       or t == SILENCE_AT_S + 60 or t == DEMO_SECONDS)
        if realtime:
            if t % show_every == 0 or interesting:
                print(render_staff_screen(hub, roster, t, acknowledged))
                print()
            time.sleep(1)
        elif interesting:
            print(render_staff_screen(hub, roster, t, acknowledged))
            print()

    # Athlete view sample: an athlete sees only their own tile.
    member_view = get_athlete_view(hub, roster, DEMO_SECONDS, role="athlete",
                                   athlete_device_id=RISE_DEVICE,
                                   requested_device_id=RISE_DEVICE)
    print(f"Athlete view (jersey {member_view['jersey']}): {member_view['label']}, "
          f"skin {member_view['skin_c']} C")
    try:
        get_athlete_view(hub, roster, DEMO_SECONDS, role="athlete",
                         athlete_device_id=RISE_DEVICE,
                         requested_device_id=SILENT_DEVICE)
    except AccessDenied as e:
        print(f"Athlete asking for a teammate's data: denied ({e})")


if __name__ == "__main__":
    run(realtime="--realtime" in sys.argv)
