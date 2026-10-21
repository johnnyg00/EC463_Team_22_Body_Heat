"""Hub data store (Subsystem E, Table E1): participants & consent, devices &
assignments, measurements (deduplicated on measurement ID), events.

Demo note: plain sqlite3 from the standard library. The December hub uses
SQLCipher (TM-23) for an encrypted file; the schema and the dedup rule are
the same. Writes can be forced to fail (set_fault) to show that reception and
alert forwarding continue while the store raises a visible fault."""

import csv
import io
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    participant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    jersey INTEGER NOT NULL,
    consent_state TEXT NOT NULL,          -- 'consented' | 'withdrawn' | 'none'
    consent_at TEXT,
    withdrawn_at TEXT
);
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    participant_id TEXT,
    session_id TEXT,
    kind TEXT NOT NULL                    -- 'pod' (real) | 'emulated'
);
CREATE TABLE IF NOT EXISTS measurements (
    measurement_id TEXT NOT NULL,
    signal TEXT NOT NULL,                 -- skin_c | air_c | humidity_pct | motion
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    value REAL,
    units TEXT NOT NULL,
    acquired_t INTEGER NOT NULL,          -- session seconds (pod clock)
    received_t INTEGER NOT NULL,          -- session seconds (hub clock)
    valid INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (measurement_id, signal)  -- AC-E5: repeated IDs never add rows
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    t INTEGER NOT NULL,
    kind TEXT NOT NULL,                   -- alert | alert_clear | no_signal | contact | battery | ack | consent | access_denied | storage_fault | session
    device_id TEXT,
    detail TEXT,
    user TEXT
);
"""

UNITS = {"skin_c": "C", "air_c": "C", "humidity_pct": "%RH", "motion": "g_rms"}


class StorageFault(Exception):
    pass


class Store:
    def __init__(self, path=":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.fault = False          # presenter control: simulate disk failure
        self.writes = 0
        self.ignored_duplicates = 0

    def set_fault(self, on):
        self.fault = bool(on)

    # -- participants / devices -------------------------------------------
    def upsert_participant(self, pid, name, jersey, consent_state, consent_at=None, withdrawn_at=None):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO participants VALUES (?,?,?,?,?,?)",
                (pid, name, jersey, consent_state, consent_at, withdrawn_at))
            self._db.commit()

    def set_consent(self, pid, consented, when):
        with self._lock:
            if consented:
                self._db.execute("UPDATE participants SET consent_state='consented', consent_at=?, withdrawn_at=NULL WHERE participant_id=?", (when, pid))
            else:
                self._db.execute("UPDATE participants SET consent_state='withdrawn', withdrawn_at=? WHERE participant_id=?", (when, pid))
            self._db.commit()

    def assign_device(self, device_id, pid, session_id, kind):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO devices VALUES (?,?,?,?)", (device_id, pid, session_id, kind))
            self._db.commit()

    # -- measurements -------------------------------------------------------
    def add_measurement_set(self, measurement_id, device_id, session_id, readings, acquired_t, received_t, valid=True):
        """Store one sample set (skin, air, humidity, motion). A repeated
        measurement ID is ignored, never duplicated (AC-E5)."""
        if self.fault:
            raise StorageFault("simulated disk write failure")
        rows = [(measurement_id, sig, device_id, session_id, readings.get(sig), UNITS[sig],
                 acquired_t, received_t, 1 if valid else 0) for sig in UNITS if sig in readings]
        with self._lock:
            before = self._db.total_changes
            self._db.executemany("INSERT OR IGNORE INTO measurements VALUES (?,?,?,?,?,?,?,?,?)", rows)
            self._db.commit()
            added = self._db.total_changes - before
        self.writes += added
        self.ignored_duplicates += len(rows) - added
        return added

    def history(self, device_id, signal=None):
        q = "SELECT signal, acquired_t, value, valid FROM measurements WHERE device_id=? ORDER BY acquired_t"
        with self._lock:
            rows = self._db.execute(q, (device_id,)).fetchall()
        if signal:
            rows = [r for r in rows if r[0] == signal]
        return rows

    def measurement_count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]

    # -- events -------------------------------------------------------------
    def add_event(self, t, kind, device_id=None, detail=None, user=None):
        if self.fault:
            raise StorageFault("simulated disk write failure")
        with self._lock:
            self._db.execute("INSERT INTO events (t, kind, device_id, detail, user) VALUES (?,?,?,?,?)",
                             (t, kind, device_id, detail, user))
            self._db.commit()

    def events(self, device_id=None, kinds=None, limit=200):
        q = "SELECT t, kind, device_id, detail, user FROM events"
        where, args = [], []
        if device_id:
            where.append("device_id=?"); args.append(device_id)
        if kinds:
            where.append("kind IN (%s)" % ",".join("?" * len(kinds))); args.extend(kinds)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [{"t": r[0], "kind": r[1], "device_id": r[2], "detail": r[3], "user": r[4]} for r in rows]

    # -- export -------------------------------------------------------------
    def export_csv(self, session_start_iso, t_to_iso):
        """All stored readings, alerts and consent records, excluding
        wearers whose consent is not current (FR-011, AC-11, AC-21)."""
        with self._lock:
            consenting = {r[0] for r in self._db.execute(
                "SELECT participant_id FROM participants WHERE consent_state='consented'")}
            dev_to_pid = dict(self._db.execute("SELECT device_id, participant_id FROM devices"))
            participants = self._db.execute(
                "SELECT participant_id, jersey, consent_state, consent_at, withdrawn_at FROM participants ORDER BY jersey").fetchall()
            meas = self._db.execute(
                "SELECT device_id, session_id, measurement_id, signal, value, units, acquired_t, received_t, valid "
                "FROM measurements ORDER BY acquired_t, device_id").fetchall()
            evs = self._db.execute(
                "SELECT t, kind, device_id, detail, user FROM events WHERE kind IN ('alert','alert_clear','ack','consent','no_signal','contact','battery','session') ORDER BY id").fetchall()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["# Body Heat export", "session_start", session_start_iso, "note",
                    "wearer IDs only; names stay in the hub roster; non-consenting wearers excluded"])
        w.writerow(["record", "participant_id", "jersey", "consent_state", "consent_at", "withdrawn_at"])
        for p in participants:
            w.writerow(["consent", *p])
        w.writerow(["record", "device_id", "session_id", "measurement_id", "signal", "value", "units", "acquired_at", "received_at", "valid"])
        for m in meas:
            if dev_to_pid.get(m[0]) in consenting:
                w.writerow(["measurement", m[0], m[1], m[2], m[3], m[4], m[5], t_to_iso(m[6]), t_to_iso(m[7]), m[8]])
        w.writerow(["record", "time", "kind", "device_id", "detail", "user"])
        for e in evs:
            if e[2] is None or dev_to_pid.get(e[2]) in consenting or e[1] in ("consent", "session"):
                w.writerow(["event", t_to_iso(e[0]), e[1], e[2], e[3], e[4]])
        return out.getvalue()
