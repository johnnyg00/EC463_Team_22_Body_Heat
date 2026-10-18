# F1 sideline view demo (synthetic data)

Team 22 Body Heat, EC463 Fall 2026. Issue #81.

Terminal demo of the Subsystem F supervisor and athlete views, driven by
synthetic pods, for the Shark Tank pitch on 10/22. Pure Python standard
library, no internet, runs on any laptop.

## Run

    python -m f1_sideline_demo.demo             # fast: prints the key moments
    python -m f1_sideline_demo.demo --realtime  # 1 update per second, like live

## Test

    python -m pytest tests/ -v

## What it shows

- Staff screen: fixed roster grid in jersey order, alert strip pinned above
  (newest first), one primary state per tile plus badges, reading age.
- The scripted scenario: jersey 11 climbs about 5 C/min, crosses the team's
  0.5 C/min trend setting at about t=20 s; the trainer acknowledges at
  t=32 s (recorded with time and user, alert stays until the pod clears it);
  the rise plateaus and the alert clears; jersey 24 stops broadcasting at
  t=10 s and shows No signal at 60 s.
- Athlete view: an athlete sees only their own tile. Access is checked in the
  backend on every request (staff vs athlete), not by hiding screen elements.
- Wellness wording only: states compare a reading with the team's own
  setting and never name a condition.

## Mirrors the Preliminary Design (10/6)

- F displays alerts the pod (C) decided and the hub (E) validated; F never
  makes a temperature decision.
- E-side rules used here: enrolled device, consent, fresh transmission
  counter, repeated measurements do not create new readings, no-signal after
  60 s without an accepted packet.
- Only changed tiles are pushed to open screens (diff_tiles).
- Athlete names live only in the display roster, never in packets.

Real BLE reception (Bleak) and SQLite storage land with E1 (issue #79,
starts 10/19); this demo swaps in the synthetic feed at the same interface.
