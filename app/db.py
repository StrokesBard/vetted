"""SQLite storage: cases, every version of every case, the vendor master, and the audit log."""
from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config
from .scenarios import SEED_VENDORS

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY, seq INTEGER, created_at TEXT, updated_at TEXT, vendor_name TEXT, country TEXT,
    channel TEXT, scenario TEXT, sample INTEGER DEFAULT 0, status TEXT, auto_status TEXT, verdict TEXT,
    top_reason TEXT, version INTEGER, work_ms INTEGER, token TEXT, override TEXT);
CREATE TABLE IF NOT EXISTS versions (
    case_id TEXT, version INTEGER, created_at TEXT, result TEXT, files TEXT, PRIMARY KEY (case_id, version));
CREATE TABLE IF NOT EXISTS vendors (
    code TEXT PRIMARY KEY, legal_name TEXT, pan TEXT, tax_id TEXT, bank_account TEXT, bank_code TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, at TEXT, actor TEXT, action TEXT, detail TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    config.DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM vendors LIMIT 1").fetchone():
            _seed_vendors(c)


def _seed_vendors(c) -> None:
    c.executemany("INSERT OR REPLACE INTO vendors VALUES (?,?,?,?,?,?,?)",
                  [(v["code"], v["legal_name"], v["pan"], v["tax_id"], v["bank_account"], v["bank_code"], "seed")
                   for v in SEED_VENDORS])


def reset() -> None:
    with conn() as c:
        for table in ("cases", "versions", "audit", "vendors"):
            c.execute(f"DELETE FROM {table}")
        _seed_vendors(c)
    shutil.rmtree(config.UPLOAD_DIR, ignore_errors=True)


def vendors(seed_only: bool = False) -> list[dict]:
    """The vendor master. Demo scenarios screen against the seed list only, so they give the same
    result however many real vendors have been onboarded since."""
    where = "WHERE source = 'seed' " if seed_only else ""
    with conn() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM vendors {where}ORDER BY code")]


def add_vendor(case_id: str, sub: dict) -> None:
    bank, tax = sub.get("bank", {}), sub.get("tax", {})
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO vendors VALUES (?,?,?,?,?,?,?)",
                  (case_id.replace("VND", "VM"), sub.get("company", {}).get("legal_name", ""), tax.get("pan", ""),
                   tax.get("gstin") or tax.get("ein") or "", bank.get("account_number", ""),
                   bank.get("ifsc") or bank.get("routing_number") or "", "onboarded"))


def new_case(*, channel: str, scenario: str | None, sample: bool, created_at: str | None = None) -> dict:
    with conn() as c:
        seq = (c.execute("SELECT COALESCE(MAX(seq), 40) FROM cases").fetchone()[0]) + 1
        case = {"id": f"VND-{seq:04d}", "seq": seq, "created_at": created_at or now(), "token": secrets.token_urlsafe(9)}
        c.execute("INSERT INTO cases (id, seq, created_at, updated_at, vendor_name, country, channel, scenario, "
                  "sample, status, version, token) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (case["id"], seq, case["created_at"], case["created_at"], "", "", channel, scenario,
                   int(sample), "running", 0, case["token"]))
    return case


def set_running(case_id: str, version: int, vendor_name: str, country: str) -> None:
    with conn() as c:
        c.execute("UPDATE cases SET status='running', version=?, vendor_name=COALESCE(NULLIF(?, ''), vendor_name), "
                  "country=COALESCE(NULLIF(?, ''), country), updated_at=? WHERE id=?",
                  (version, vendor_name, country, now(), case_id))


def save_version(case_id: str, version: int, result: dict, files: list[dict], at: str | None = None) -> None:
    at = at or now()
    sub = result["submission"]
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO versions VALUES (?,?,?,?,?)",
                  (case_id, version, at, json.dumps(result), json.dumps(files)))
        c.execute("UPDATE cases SET status=?, auto_status=?, verdict=?, top_reason=?, version=?, work_ms=?, "
                  "vendor_name=?, country=?, updated_at=?, override=NULL WHERE id=?",
                  (result["status"], result["status"], result["verdict"], result["top_reason"], version,
                   result["work_ms"], sub.get("company", {}).get("legal_name") or "Unnamed vendor",
                   result["country"], at, case_id))


def mark_error(case_id: str, message: str) -> None:
    with conn() as c:
        c.execute("UPDATE cases SET status='error', verdict=?, updated_at=? WHERE id=?", (message, now(), case_id))


def log(case_id: str, actor: str, action: str, detail: str = "", at: str | None = None) -> None:
    with conn() as c:
        c.execute("INSERT INTO audit (case_id, at, actor, action, detail) VALUES (?,?,?,?,?)",
                  (case_id, at or now(), actor, action, detail))


def get_case(case_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return None
        case = dict(row)
        case["override"] = json.loads(case["override"]) if case["override"] else None
        case["versions"] = [
            {"version": r["version"], "created_at": r["created_at"], "result": json.loads(r["result"]),
             "files": json.loads(r["files"])}
            for r in c.execute("SELECT * FROM versions WHERE case_id=? ORDER BY version", (case_id,))]
        case["audit"] = [dict(r) for r in c.execute("SELECT at, actor, action, detail FROM audit WHERE case_id=? ORDER BY id", (case_id,))]
        return case


def get_version(case_id: str, version: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM versions WHERE case_id=? AND version=?", (case_id, version)).fetchone()
        return {"result": json.loads(r["result"]), "files": json.loads(r["files"])} if r else None


def list_cases() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, created_at, updated_at, vendor_name, country, channel, scenario, sample, status, auto_status, "
            "verdict, top_reason, version, work_ms, override IS NOT NULL AS overridden FROM cases ORDER BY created_at DESC, seq DESC")]


def latest_results() -> list[dict]:
    with conn() as c:
        return [json.loads(r["result"]) for r in c.execute(
            "SELECT v.result FROM versions v JOIN cases c ON c.id = v.case_id AND c.version = v.version")]


def set_override(case_id: str, status: str, reason: str, reviewer: str) -> None:
    with conn() as c:
        c.execute("UPDATE cases SET status=?, override=?, updated_at=? WHERE id=?",
                  (status, json.dumps({"status": status, "reason": reason, "reviewer": reviewer, "at": now()}),
                   now(), case_id))
