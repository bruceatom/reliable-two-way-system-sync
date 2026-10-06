"""Durable inbox and append-only audit history, separate from both systems."""
import json
import sqlite3
from .transform import canonical, fingerprint


class EventConflict(ValueError):
    pass


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, direction TEXT NOT NULL, payload TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, error TEXT
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT NOT NULL,
                at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE IF NOT EXISTS conflicts (
                event_id TEXT NOT NULL, digest TEXT NOT NULL, direction TEXT NOT NULL,
                payload TEXT NOT NULL, UNIQUE(event_id,digest)
            );
        """)

    def close(self):
        self.db.close()

    def record(self, event_id, action, detail=""):
        with self.db:
            self.db.execute("INSERT INTO audit(event_id,action,detail) VALUES (?,?,?)",
                            (event_id, action, detail))

    def ingest(self, event_id, direction, payload):
        digest = fingerprint({"direction": direction, "payload": payload})
        row = self.get(event_id)
        if row:
            if row["digest"] != digest:
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO conflicts VALUES (?,?,?,?)",
                                    (event_id, digest, direction, canonical(payload)))
                self.record(event_id, "event_conflict", canonical(payload))
                raise EventConflict("event ID reused with different direction or payload")
            self.record(event_id, "redelivered", row["state"])
            return row
        with self.db:
            self.db.execute("INSERT INTO events(event_id,direction,payload,digest) VALUES (?,?,?,?)",
                            (event_id, direction, canonical(payload), digest))
        self.record(event_id, "received")
        return self.get(event_id)

    def get(self, event_id):
        row = self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return dict(row) if row else None

    def transition(self, event_id, state, error=None, attempted=False):
        with self.db:
            self.db.execute("UPDATE events SET state=?,error=?,attempts=attempts+? WHERE event_id=?",
                            (state, error, int(attempted), event_id))
            self.db.execute("INSERT INTO audit(event_id,action,detail) VALUES (?,?,?)",
                            (event_id, state, error or ""))

    def recoverable(self):
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM events WHERE state IN ('pending','retry') ORDER BY event_id")]

    def summary(self):
        states = {row[0]: row[1] for row in self.db.execute(
            "SELECT state,COUNT(*) FROM events GROUP BY state ORDER BY state")}
        conflicts = self.db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
        if conflicts:
            states["conflict"] = conflicts
        return states

    def audit(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM audit ORDER BY id")]
