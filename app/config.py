"""Settings. Values come from environment variables, or from a local .env file (never committed)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("VETTED_DATA_DIR", ROOT / "data"))
SCENARIO_DIR = ROOT / "data" / "scenarios"
UPLOAD_DIR = DATA / "uploads"
DB_PATH = DATA / "vetted.db"
STATIC_DIR = ROOT / "static"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# The company doing the onboarding (appears in vendor messages).
BUYER_NAME = os.environ.get("BUYER_NAME", "Acme Retail India")

# AI document reader. "auto" uses an AI model when a key is configured and falls back to rules;
# "off" never calls a model.
AI_MODE = os.environ.get("AI_MODE", "auto").lower()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest,gemini-3.5-flash-lite,gemini-3.1-flash-lite")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# Seconds to wait for one model, and in total across the failover chain, before using the rule-based reader.
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "9"))
AI_BUDGET = float(os.environ.get("AI_BUDGET", "14"))
# Reuse the AI reading when the exact same file is seen again (saves free-tier quota).
AI_CACHE = os.environ.get("AI_CACHE", "on").lower() != "off"

# Minimum time each stage stays on screen in the live run view, so a human can follow it.
STAGE_DELAY = float(os.environ.get("STAGE_DELAY", "0.5"))

# Used for the "reviewer time saved" estimate on the dashboard.
MANUAL_MINUTES_PER_CASE = int(os.environ.get("MANUAL_MINUTES_PER_CASE", "25"))
