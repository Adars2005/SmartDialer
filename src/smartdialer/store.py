"""SQLite source of truth with serialized, atomic reservation transactions."""
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .domain import AGENT_TRANSITIONS, TERMINAL_CALL_STATES, valid_call_transition


class Store:
    def __init__(self, path: str = "smartdialer.db"):
        self.uri = path == ":memory:"
        self.path = f"file:smartdialer-{uuid.uuid4().hex}?mode=memory&cache=shared" if self.uri else str(path)
        # A keeper connection preserves the shared in-memory DB across unit-of-work connections.
        self._keeper = sqlite3.connect(self.path, uri=True) if self.uri else None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None, uri=self.uri)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @contextmanager
    def tx(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def session(self):
        db = self.connect()
        try:
            yield db
        finally:
            db.close()

    def _init(self):
        with self.session() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS borrowers(id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'QUEUED', updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(
              id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), borrower_id TEXT NOT NULL REFERENCES borrowers(id),
              state TEXT NOT NULL, provider TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
              lease_until REAL NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, reason TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_call_per_agent ON calls(agent_id)
              WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED');
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_call_per_borrower ON calls(borrower_id)
              WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED');
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, event_type TEXT NOT NULL,
              received REAL NOT NULL, outcome TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, created REAL NOT NULL, requested INTEGER NOT NULL,
              approved INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stats(key TEXT PRIMARY KEY, value REAL NOT NULL);
            """)

    def add_agent(self, agent_id):
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?)", (agent_id, "AVAILABLE", time.time()))

    def add_borrower(self, borrower_id):
        with self.tx() as db:
            db.execute("INSERT OR IGNORE INTO borrowers VALUES(?,?,?)", (borrower_id, "QUEUED", time.time()))

    def counts(self):
        with self.session() as db:
            agents = dict(db.execute("SELECT state,COUNT(*) n FROM agents GROUP BY state").fetchall())
            calls = dict(db.execute("SELECT state,COUNT(*) n FROM calls GROUP BY state").fetchall())
            borrowers = dict(db.execute("SELECT state,COUNT(*) n FROM borrowers GROUP BY state").fetchall())
            return {"agents": agents, "calls": calls, "borrowers": borrowers}

    def snapshot(self):
        c = self.counts()
        active_states = ("RESERVED", "INITIATED", "RINGING", "ANSWERED", "CONNECTED", "RECONCILING")
        c["active_calls"] = sum(c["calls"].get(s, 0) for s in active_states)
        c["free_agents"] = c["agents"].get("AVAILABLE", 0)
        c["queued_borrowers"] = c["borrowers"].get("QUEUED", 0)
        c["ringing"] = c["calls"].get("RINGING", 0) + c["calls"].get("INITIATED", 0)
        c["connected"] = c["calls"].get("CONNECTED", 0) + c["calls"].get("ANSWERED", 0)
        return c

    def reserve(self, provider: str, lease_seconds=25):
        now = time.time()
        call_id = str(uuid.uuid4())
        with self.tx() as db:
            agent = db.execute("SELECT id FROM agents WHERE state='AVAILABLE' ORDER BY id LIMIT 1").fetchone()
            borrower = db.execute("SELECT id FROM borrowers WHERE state='QUEUED' ORDER BY id LIMIT 1").fetchone()
            if not agent or not borrower:
                return None
            # BEGIN IMMEDIATE serializes writers; conditional updates document and enforce the claim.
            if db.execute("UPDATE agents SET state='RESERVED',updated=? WHERE id=? AND state='AVAILABLE'", (now, agent["id"])).rowcount != 1:
                return None
            if db.execute("UPDATE borrowers SET state='RESERVED',updated=? WHERE id=? AND state='QUEUED'", (now, borrower["id"])).rowcount != 1:
                return None
            db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (call_id, agent["id"], borrower["id"], "RESERVED", provider, now, now, now + lease_seconds, 1, None))
            return call_id

    def initiate(self, call_id):
        now = time.time()
        with self.tx() as db:
            row = db.execute("SELECT agent_id,state FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["state"] != "RESERVED":
                return False
            if db.execute("UPDATE agents SET state='DIALING',updated=? WHERE id=? AND state='RESERVED'", (now, row["agent_id"])).rowcount != 1:
                return False
            db.execute("UPDATE calls SET state='INITIATED',updated=?,lease_until=? WHERE id=?", (now, now + 45, call_id))
            return True

    def fail_initiation(self, call_id, reason):
        self._finish(call_id, "FAILED", reason)

    def _finish(self, call_id, final_state, reason=None):
        now = time.time()
        with self.tx() as db:
            row = db.execute("SELECT agent_id,borrower_id,state FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["state"] in TERMINAL_CALL_STATES:
                return False
            db.execute("UPDATE calls SET state=?,updated=?,reason=? WHERE id=?", (final_state, now, reason, call_id))
            db.execute("UPDATE agents SET state='WRAP_UP',updated=? WHERE id=?", (now, row["agent_id"]))
            db.execute("UPDATE borrowers SET state=?,updated=? WHERE id=?",
                       ("DONE" if final_state == "COMPLETED" else "QUEUED", now, row["borrower_id"]))
            return True

    def apply_event(self, call_id, event_id, target):
        now = time.time()
        with self.tx() as db:
            if db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
                return "duplicate"
            row = db.execute("SELECT agent_id,borrower_id,state FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row:
                db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (event_id, call_id, target, now, "unknown_call"))
                return "unknown_call"
            state = row["state"]
            if state in TERMINAL_CALL_STATES or not valid_call_transition(state, target):
                db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (event_id, call_id, target, now, "ignored_stale"))
                return "ignored_stale"
            db.execute("UPDATE calls SET state=?,updated=?,lease_until=? WHERE id=?", (target, now, now + 45, call_id))
            agent_state = {"RINGING": "DIALING", "ANSWERED": "CONNECTED", "CONNECTED": "CONNECTED",
                           "COMPLETED": "WRAP_UP", "FAILED": "WRAP_UP", "CANCELLED": "WRAP_UP"}.get(target)
            if agent_state:
                db.execute("UPDATE agents SET state=?,updated=? WHERE id=?", (agent_state, now, row["agent_id"]))
            if target in TERMINAL_CALL_STATES:
                borrower_state = "DONE" if target == "COMPLETED" else "QUEUED"
                db.execute("UPDATE borrowers SET state=?,updated=? WHERE id=?", (borrower_state, now, row["borrower_id"]))
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (event_id, call_id, target, now, "applied"))
            return "applied"

    def release_wrap_up(self):
        with self.tx() as db:
            return db.execute("UPDATE agents SET state='AVAILABLE',updated=? WHERE state='WRAP_UP'", (time.time(),)).rowcount

    def set_agent_state(self, agent_id, state):
        with self.tx() as db:
            now = time.time()
            current_row = db.execute("SELECT state FROM agents WHERE id=?", (agent_id,)).fetchone()
            if not current_row:
                return 0
            current = current_row["state"]
            if state != current and state not in AGENT_TRANSITIONS.get(current, set()):
                return 0
            if state == "OFFLINE":
                call = db.execute("SELECT id,borrower_id,state FROM calls WHERE agent_id=? AND state NOT IN ('COMPLETED','FAILED','CANCELLED')", (agent_id,)).fetchone()
                if call:
                    # During setup, release the borrower for safe retry. For an already connected
                    # call, hold it for reconciliation rather than risking an immediate redial.
                    borrower_state = "HOLD" if call["state"] in ("ANSWERED", "CONNECTED") else "QUEUED"
                    db.execute("UPDATE calls SET state='FAILED',updated=?,reason='agent disappeared; provider reconciliation required' WHERE id=?",
                               (now, call["id"]))
                    db.execute("UPDATE borrowers SET state=?,updated=? WHERE id=?",
                               (borrower_state, now, call["borrower_id"]))
                return db.execute("UPDATE agents SET state='OFFLINE',updated=? WHERE id=?", (now, agent_id)).rowcount
            return db.execute("UPDATE agents SET state=?,updated=? WHERE id=?", (state, now, agent_id)).rowcount

    def recover_expired(self):
        now = time.time()
        with self.tx() as db:
            rows = db.execute("SELECT id,agent_id,borrower_id,state FROM calls WHERE lease_until < ? AND state NOT IN ('COMPLETED','FAILED','CANCELLED')", (now,)).fetchall()
            for row in rows:
                if row["state"] == "RESERVED":
                    # Nothing has reached the provider yet, so releasing this lease is safe.
                    db.execute("UPDATE calls SET state='FAILED',updated=?,reason='reservation lease expired before initiation' WHERE id=?", (now, row['id']))
                    db.execute("UPDATE agents SET state='AVAILABLE',updated=? WHERE id=? AND state='RESERVED'", (now, row['agent_id']))
                    db.execute("UPDATE borrowers SET state='QUEUED',updated=? WHERE id=?", (now, row['borrower_id']))
                else:
                    # The provider may have accepted or connected the call. Retain both
                    # resources until a webhook or explicit provider lookup resolves it.
                    db.execute("UPDATE calls SET state='RECONCILING',updated=?,lease_until=?,reason='lease expired; provider status reconciliation required' WHERE id=?",
                               (now, now + 45, row['id']))
                    db.execute("UPDATE borrowers SET state='HOLD',updated=? WHERE id=?", (now, row['borrower_id']))
            return len(rows)

    def resolve_reconciliation(self, call_id, provider_state):
        """Apply an authoritative provider lookup to a call in RECONCILING."""
        state_map = {"not_found": "FAILED", "failed": "FAILED", "completed": "COMPLETED",
                     "ringing": "RINGING", "answered": "ANSWERED", "connected": "CONNECTED"}
        target = state_map.get(str(provider_state).lower())
        if target is None:
            raise ValueError(f"unsupported provider reconciliation state: {provider_state}")
        now = time.time()
        with self.tx() as db:
            row = db.execute("SELECT agent_id,borrower_id,state FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row or row["state"] != "RECONCILING":
                return False
            db.execute("UPDATE calls SET state=?,updated=?,lease_until=?,reason=? WHERE id=?",
                       (target, now, now + 45, f"provider reconciliation: {provider_state}", call_id))
            agent_state = {"RINGING": "DIALING", "ANSWERED": "CONNECTED", "CONNECTED": "CONNECTED",
                           "FAILED": "WRAP_UP", "COMPLETED": "WRAP_UP"}[target]
            db.execute("UPDATE agents SET state=?,updated=? WHERE id=?", (agent_state, now, row["agent_id"]))
            if target in TERMINAL_CALL_STATES:
                borrower_state = "DONE" if target == "COMPLETED" else "QUEUED"
                db.execute("UPDATE borrowers SET state=?,updated=? WHERE id=?", (borrower_state, now, row["borrower_id"]))
            return True

    def record_decision(self, requested, approved, action, reason, details):
        import json
        with self.tx() as db:
            db.execute("INSERT INTO decisions(created,requested,approved,action,reason,details) VALUES(?,?,?,?,?,?)",
                       (time.time(), requested, approved, action, reason, json.dumps(details, sort_keys=True)))

    def decisions(self):
        with self.session() as db:
            return [dict(r) for r in db.execute("SELECT * FROM decisions ORDER BY id")]

    def active_calls(self):
        with self.session() as db:
            return [dict(r) for r in db.execute("SELECT * FROM calls WHERE state NOT IN ('COMPLETED','FAILED','CANCELLED')")]

