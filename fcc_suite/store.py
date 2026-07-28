"""
Case management + tamper-evident audit log (SQLite, stdlib only).

Why this exists
---------------
FSC / FIU inspection expects a licensee to *evidence its decisions*: who screened
whom, what the system returned, how a possible match was adjudicated, the rationale,
and what action followed (EDD, escalation, STR). This module records exactly that.

Audit integrity
---------------
The audit_log is append-only and hash-chained: each event stores the hash of the
previous event, so any later edit or deletion breaks the chain and is detectable
via verify_audit_chain(). That is the difference between "we have logs" and
"we have logs an inspector can trust".
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DB_PATH = os.environ.get("FCC_DB", str(Path.cwd() / "fcc_runtime.db"))

CASE_STATUSES = ["open", "edd", "escalated", "str_filed", "closed"]
ADJUDICATIONS = ["unreviewed", "true_match", "false_positive", "needs_review"]
GENESIS = "0" * 64


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref TEXT UNIQUE NOT NULL,
            subject TEXT NOT NULL,
            subject_type TEXT NOT NULL DEFAULT 'individual',
            status TEXT NOT NULL DEFAULT 'open',
            notes TEXT DEFAULT '',
            created_ts REAL NOT NULL,
            created_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS screenings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            ts REAL NOT NULL,
            actor TEXT NOT NULL,
            query TEXT NOT NULL,
            is_entity INTEGER NOT NULL DEFAULT 0,
            band TEXT NOT NULL,
            top_score REAL NOT NULL,
            hits_json TEXT NOT NULL,
            adjudication TEXT NOT NULL DEFAULT 'unreviewed',
            rationale TEXT DEFAULT '',
            FOREIGN KEY(case_id) REFERENCES cases(id)
        );
        CREATE TABLE IF NOT EXISTS risk_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            ts REAL NOT NULL,
            actor TEXT NOT NULL,
            score REAL NOT NULL,
            band TEXT NOT NULL,
            pillars TEXT NOT NULL,
            factors_json TEXT NOT NULL,
            note TEXT DEFAULT '',
            FOREIGN KEY(case_id) REFERENCES cases(id)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            details TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            row_hash TEXT NOT NULL
        );
        """)


# ---- audit log (append-only, hash-chained) --------------------------------

def _last_hash(con) -> str:
    row = con.execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return row["row_hash"] if row else GENESIS


def _hash_event(prev: str, ts: float, actor: str, action: str,
                etype: str, eid: str, details: str) -> str:
    blob = f"{prev}|{ts:.6f}|{actor}|{action}|{etype}|{eid}|{details}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def log_event(actor: str, action: str, entity_type: str, entity_id: str,
              details: dict | None = None) -> None:
    ts = time.time()
    payload = json.dumps(details or {}, sort_keys=True, default=str)
    with _conn() as con:
        prev = _last_hash(con)
        rh = _hash_event(prev, ts, actor, action, entity_type, str(entity_id), payload)
        con.execute(
            "INSERT INTO audit_log(ts,actor,action,entity_type,entity_id,details,prev_hash,row_hash)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (ts, actor, action, entity_type, str(entity_id), payload, prev, rh),
        )


def verify_audit_chain() -> tuple[bool, int | None]:
    """Recompute the chain. Returns (ok, first_broken_id_or_None)."""
    with _conn() as con:
        rows = con.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
    prev = GENESIS
    for r in rows:
        expect = _hash_event(prev, r["ts"], r["actor"], r["action"],
                             r["entity_type"], r["entity_id"], r["details"])
        if expect != r["row_hash"] or r["prev_hash"] != prev:
            return False, r["id"]
        prev = r["row_hash"]
    return True, None


def get_audit_trail(case_id: int | None = None, limit: int = 500) -> list[dict]:
    with _conn() as con:
        if case_id is not None:
            rows = con.execute(
                "SELECT * FROM audit_log WHERE entity_type='case' AND entity_id=?"
                " OR (entity_type IN ('screening','risk') AND json_extract(details,'$.case_id')=?)"
                " ORDER BY id DESC LIMIT ?",
                (str(case_id), case_id, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---- cases ----------------------------------------------------------------

def create_case(ref: str, subject: str, subject_type: str, actor: str,
                notes: str = "") -> int:
    ts = time.time()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO cases(ref,subject,subject_type,status,notes,created_ts,created_by)"
            " VALUES(?,?,?,?,?,?,?)",
            (ref, subject, subject_type, "open", notes, ts, actor))
        case_id = cur.lastrowid
    log_event(actor, "case.create", "case", case_id,
              {"ref": ref, "subject": subject, "subject_type": subject_type})
    return case_id


def list_cases(status: str | None = None) -> list[dict]:
    with _conn() as con:
        if status:
            rows = con.execute("SELECT * FROM cases WHERE status=? ORDER BY created_ts DESC",
                               (status,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM cases ORDER BY created_ts DESC").fetchall()
    return [dict(r) for r in rows]


def get_case(case_id: int) -> dict | None:
    with _conn() as con:
        r = con.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    return dict(r) if r else None


def update_case_status(case_id: int, status: str, actor: str) -> None:
    with _conn() as con:
        con.execute("UPDATE cases SET status=? WHERE id=?", (status, case_id))
    log_event(actor, "case.status", "case", case_id, {"status": status})


# ---- screenings & risk snapshots ------------------------------------------

def add_screening(case_id: int, actor: str, query: str, is_entity: bool,
                  band: str, top_score: float, hits: list[dict],
                  adjudication: str = "unreviewed", rationale: str = "") -> int:
    ts = time.time()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO screenings(case_id,ts,actor,query,is_entity,band,top_score,"
            "hits_json,adjudication,rationale) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (case_id, ts, actor, query, int(is_entity), band, top_score,
             json.dumps(hits, default=str), adjudication, rationale))
        sid = cur.lastrowid
    log_event(actor, "screening.record", "screening", sid,
              {"case_id": case_id, "query": query, "band": band,
               "top_score": top_score, "adjudication": adjudication,
               "rationale": rationale})
    return sid


def adjudicate_screening(screening_id: int, adjudication: str, rationale: str,
                         actor: str) -> None:
    if adjudication not in ADJUDICATIONS:
        raise ValueError("invalid adjudication")
    with _conn() as con:
        row = con.execute("SELECT case_id FROM screenings WHERE id=?",
                          (screening_id,)).fetchone()
        con.execute("UPDATE screenings SET adjudication=?, rationale=? WHERE id=?",
                    (adjudication, rationale, screening_id))
    log_event(actor, "screening.adjudicate", "screening", screening_id,
              {"case_id": row["case_id"] if row else None,
               "adjudication": adjudication, "rationale": rationale})


def add_risk(case_id: int, actor: str, score: float, band: str,
             pillars: list[str], factors: list[dict], note: str = "") -> int:
    ts = time.time()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO risk_assessments(case_id,ts,actor,score,band,pillars,"
            "factors_json,note) VALUES(?,?,?,?,?,?,?,?)",
            (case_id, ts, actor, score, band, ",".join(pillars),
             json.dumps(factors, default=str), note))
        rid = cur.lastrowid
    log_event(actor, "risk.record", "risk", rid,
              {"case_id": case_id, "score": score, "band": band,
               "pillars": pillars})
    return rid


def get_case_screenings(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM screenings WHERE case_id=? ORDER BY ts DESC",
                           (case_id,)).fetchall()
    return [dict(r) for r in rows]


def get_case_risks(case_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM risk_assessments WHERE case_id=? ORDER BY ts DESC",
                           (case_id,)).fetchall()
    return [dict(r) for r in rows]


# ---- export ---------------------------------------------------------------

def export_case(case_id: int) -> dict:
    case = get_case(case_id)
    return {
        "case": case,
        "screenings": get_case_screenings(case_id),
        "risk_assessments": get_case_risks(case_id),
        "audit_trail": get_audit_trail(case_id),
        "exported_at": time.time(),
    }


def stats() -> dict:
    with _conn() as con:
        c = con.execute("SELECT COUNT(*) n FROM cases").fetchone()["n"]
        s = con.execute("SELECT COUNT(*) n FROM screenings").fetchone()["n"]
        a = con.execute("SELECT COUNT(*) n FROM audit_log").fetchone()["n"]
        openc = con.execute("SELECT COUNT(*) n FROM cases WHERE status!='closed'").fetchone()["n"]
    return {"cases": c, "open_cases": openc, "screenings": s, "audit_events": a}
