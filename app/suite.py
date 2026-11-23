"""Runs every demo scenario without saving anything and compares the outcome with what we expect.

Used by the "Test suite" page and by pytest. It is the proof that the edge cases are deliberate.
"""
from __future__ import annotations

from . import config
from .docgen import build_all
from .pipeline import Pipeline, merge_fix
from .scenarios import BLOCKLIST, SCENARIOS, SEED_VENDORS


def ensure_documents() -> None:
    if not all((config.SCENARIO_DIR / key / spec["file"]).exists()
               for key, sc in SCENARIOS.items() for spec in sc["docs"] + sc.get("fix_docs", [])):
        build_all(config.SCENARIO_DIR)


def scenario_files(key: str) -> list[dict]:
    sc = SCENARIOS[key]
    return [{"slot": d["slot"], "path": config.SCENARIO_DIR / key / d["file"], "name": d["file"]} for d in sc["docs"]]


def run_scenario(key: str, *, vendors=None, use_ai=False) -> dict:
    """Returns {"first": result, "after_fix": result | None}."""
    sc = SCENARIOS[key]
    vendors = SEED_VENDORS if vendors is None else vendors
    files = scenario_files(key)
    first = Pipeline(sc["submission"], files, channel=sc["channel"], vendors=vendors,
                     blocklist=BLOCKLIST, use_ai=use_ai).run()
    after = None
    if sc.get("fix"):
        fixed = merge_fix(first["submission"], sc["fix"]["fields"])
        by_slot = {f["slot"]: f for f in files}
        for d in sc["fix"]["docs"]:
            by_slot[d["slot"]] = {"slot": d["slot"], "path": config.SCENARIO_DIR / key / d["file"], "name": d["file"]}
        after = Pipeline(fixed, list(by_slot.values()), channel="portal", vendors=vendors,
                         blocklist=BLOCKLIST, use_ai=use_ai).run()
    return {"first": first, "after_fix": after}


def run_all(use_ai=False) -> list[dict]:
    ensure_documents()
    rows = []
    for key, sc in SCENARIOS.items():
        out = run_scenario(key, use_ai=use_ai)
        first, after = out["first"], out["after_fix"]
        row = {
            "key": key, "title": sc["title"], "kind": sc["kind"],
            "expected": sc["expect"], "actual": first["status"], "ok": first["status"] == sc["expect"],
            "reason": first["top_reason"], "ms": first["work_ms"],
            "checks": len(first["findings"]),
        }
        if sc.get("expect_after_fix"):
            row.update(expected_after_fix=sc["expect_after_fix"], actual_after_fix=after["status"],
                       ok=row["ok"] and after["status"] == sc["expect_after_fix"])
        rows.append(row)
    return rows
