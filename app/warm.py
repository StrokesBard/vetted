"""Pre-read the demo documents with the AI reader, so live demo runs are instant and use no quota.

Readings are cached by file content (data/ai_cache.json). The UI labels them "(cached)".
Run before a demo:  python -m app.warm
"""
from __future__ import annotations

import time

from . import config, llm, suite
from .docreader import read_documents
from .scenarios import SCENARIOS


def main() -> None:
    if not llm.provider():
        print("No AI key configured (see .env.example). Nothing to warm; the rule-based reader needs no warm-up.")
        return
    suite.ensure_documents()
    for key, sc in SCENARIOS.items():
        paths = [config.SCENARIO_DIR / key / d["file"] for d in sc["docs"] + sc.get("fix_docs", [])]
        for attempt in range(1, 4):
            readings = read_documents(paths, use_ai=True)
            done = sum(r["reader"].startswith("ai") for r in readings)
            print(f"{key:16} {done}/{len(paths)} documents read by AI" + ("" if done == len(paths) else f"  (attempt {attempt}, waiting for the rate limit)"))
            if done == len(paths):
                break
            time.sleep(llm.REST_SECONDS + 2)
    print("Done. Scenario runs will now show the reader as cached.")


if __name__ == "__main__":
    main()
