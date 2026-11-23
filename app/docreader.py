"""Reads a vendor document: what kind of document is it, and what does it say?

Two readers work together:
  - a rule-based reader (keywords + patterns over the PDF text layer): free, instant, repeatable
  - an AI reader (vision model): handles scans, odd layouts, and anything the rules can't parse

Guardrail: when the PDF has a text layer, any identifier the AI returns must literally appear
in that text. If it doesn't, the AI value is discarded. The AI can't invent a GSTIN.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

import pymupdf

from . import config, llm
from .validators import GST_STATE_CODES, norm_id

DOC_LABELS = {
    "gst_certificate": "GST registration certificate",
    "incorporation_certificate": "certificate of incorporation",
    "cancelled_cheque": "cancelled cheque",
    "bank_letter": "bank letter",
    "irs_ein_letter": "IRS EIN letter",
    "invoice": "invoice",
    "other": "unrecognised document",
    "unreadable": "unreadable file",
}

_KEYWORDS = {
    "gst_certificate": ["FORM GST REG-06", "REGISTRATION CERTIFICATE", "LEGAL NAME", "CONSTITUTION OF BUSINESS"],
    "incorporation_certificate": ["CERTIFICATE OF INCORPORATION", "NAME OF THE ENTITY", "INCORPORATED"],
    "cancelled_cheque": ["IFSC", "A/C NO", "RUPEES", "CANCELLED", "OR BEARER"],
    "invoice": ["TAX INVOICE", "INVOICE NO", "BILL TO", "QTY", "TOTAL"],
    "irs_ein_letter": ["INTERNAL REVENUE SERVICE", "EMPLOYER IDENTIFICATION NUMBER", "CP 575"],
    "bank_letter": ["BANK ACCOUNT VERIFICATION", "ROUTING NUMBER", "ACCOUNT HOLDER NAME", "ACCOUNT NUMBER"],
}

_US_STATES = {"TX": "Texas", "CA": "California", "NY": "New York", "DE": "Delaware", "WA": "Washington",
              "MA": "Massachusetts", "IL": "Illinois", "FL": "Florida", "NJ": "New Jersey", "GA": "Georgia"}

ID_FIELDS = ("gstin", "pan", "ein", "account_number", "ifsc", "routing_number")


def _text_of(path: Path) -> str:
    try:
        with pymupdf.open(str(path)) as doc:
            return "\n".join(page.get_text() for page in doc).strip()
    except Exception:
        return ""


def _mime(path: Path) -> str:
    return {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "application/pdf")


def classify_by_rules(text: str) -> tuple[str, float]:
    upper = text.upper()
    if len(upper) < 20:
        return "unreadable", 0.0
    scores = {t: sum(k in upper for k in kws) / len(kws) for t, kws in _KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return (best, round(scores[best], 2)) if scores[best] >= 0.5 else ("other", round(scores[best], 2))


def _after(label_pattern: str, text: str) -> str | None:
    m = re.search(label_pattern + r"\s*:\s*(.+)", text, re.I)
    return m.group(1).strip() if m else None


def _block_after(label_pattern: str, lines: list[str], n: int = 2) -> str | None:
    for i, line in enumerate(lines):
        if re.search(label_pattern, line, re.I):
            return ", ".join(l.strip() for l in lines[i + 1:i + 1 + n] if l.strip()) or None
    return None


def _state_and_pin(address: str | None) -> tuple[str | None, str | None]:
    if not address:
        return None, None
    state = next((s for s in GST_STATE_CODES.values() if s.lower() in address.lower()), None)
    pin = re.search(r"\b\d{6}\b", address)
    if not state:
        m = re.search(r"\b([A-Z]{2})\s+(\d{5})\b", address)
        if m and m.group(1) in _US_STATES:
            return _US_STATES[m.group(1)], m.group(2)
    return state, pin.group(0) if pin else None


def extract_by_rules(text: str, doc_type: str) -> dict:
    lines = text.splitlines()
    f: dict = {}

    def first(pattern):
        m = re.search(pattern, text)
        return m.group(0) if m else None

    f["gstin"] = first(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
    f["pan"] = first(r"\b[A-Z]{5}\d{4}[A-Z]\b")
    f["ifsc"] = first(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
    f["ein"] = first(r"\b\d{2}-\d{7}\b")

    if doc_type == "gst_certificate":
        f["legal_name"] = _after(r"Legal Name", text)
        f["trade_name"] = _after(r"Trade Name, if any", text)
        f["constitution"] = _after(r"Constitution of Business", text)
        f["address"] = _block_after(r"Address of Principal Place of Business", lines)
    elif doc_type == "incorporation_certificate":
        f["legal_name"] = _after(r"Name of the entity", text)
        f["registration_id"] = (_after(r"\(CIN\)", text) or _after(r"\(LLPIN\)", text)
                                or _after(r"State File Number", text))
        f["address"] = _block_after(r"(Registered|Principal) office address", lines)
    elif doc_type == "cancelled_cheque":
        f["bank_name"] = lines[0].strip() if lines else None
        m = re.search(r"A/c No\.?\s*([0-9 ]{6,22})", text, re.I)
        f["account_number"] = m.group(1).replace(" ", "") if m else None
        for i, line in enumerate(lines):
            if re.search(r"Authorised Signatory|Please sign above", line, re.I) and i > 0:
                f["account_holder"] = re.sub(r"^For\s+", "", lines[i - 1].strip(), flags=re.I)
                break
    elif doc_type == "bank_letter":
        f["bank_name"] = lines[0].strip() if lines else None
        f["account_holder"] = _after(r"Account Holder Name", text)
        f["account_number"] = _after(r"Account Number", text)
        f["routing_number"] = _after(r"Routing Number", text)
        f["address"] = _block_after(r"Account Address", lines)
    elif doc_type == "irs_ein_letter":
        for i, line in enumerate(lines):
            if "CP 575" in line and i + 1 < len(lines):
                f["legal_name"] = lines[i + 1].strip()
                f["address"] = ", ".join(l.strip() for l in lines[i + 2:i + 4])
                break
    elif doc_type == "invoice":
        f["invoice_number"] = _after(r"Invoice No", text)

    f["state"], f["pincode"] = _state_and_pin(f.get("address"))
    return {k: v for k, v in f.items() if v}


def model_label(model: str) -> str:
    """'gemini-3.5-flash-lite' -> 'Gemini 3.5 Flash Lite'; 'llama-3.3-70b-versatile' -> 'Llama 3.3 70B'."""
    words = [w for w in model.split("-") if w not in ("latest", "versatile", "preview")]
    return " ".join(w.upper() if re.fullmatch(r"\d+b", w) else w.capitalize() if w[:1].isalpha() else w
                    for w in words)


def reader_label(reader: str) -> str:
    """Turn an internal reader tag ('ai:gemini-flash-lite-latest+rules (cached)') into plain words."""
    if not reader.startswith("ai:"):
        return "the rule-based reader (no AI)"
    model = re.match(r"ai:([^+ ]+)", reader)
    label = f"AI ({model_label(model.group(1)) if model else 'model'})"
    return label + (" · reused from an earlier read" if "(cached)" in reader else "")


def _rule_reading(path: Path) -> tuple[dict, str]:
    text = _text_of(path)
    has_text = len(text) >= 20
    rule_type, rule_conf = classify_by_rules(text)
    rule_fields = extract_by_rules(text, rule_type) if has_text else {}
    return ({"doc_type": rule_type, "confidence": rule_conf, "fields": rule_fields, "reader": "rules",
             "notes": [], "has_text": has_text, "file": path.name}, text)


def _merge_ai(result: dict, ai: dict, text: str, cached: bool) -> dict:
    """Combine the AI reading with the rule reading, applying the guardrail."""
    has_text, rule_type, rule_conf = result["has_text"], result["doc_type"], result["confidence"]
    rule_fields = result["fields"]
    result["reader"] = f"ai:{ai['model']}" + ("+rules" if has_text else "") + (" (cached)" if cached else "")
    # Classification: trust the AI unless the rules are confident and disagree.
    if has_text and rule_conf >= 0.75 and ai["doc_type"] != rule_type:
        result["notes"].append(f"AI classified this as {ai['doc_type']} but the text clearly matches "
                               f"{rule_type}; kept {rule_type}.")
    else:
        result["doc_type"] = ai["doc_type"]
        result["confidence"] = round(ai["confidence"], 2) if not has_text else max(rule_conf, round(ai["confidence"], 2))

    fields = dict(rule_fields)
    flat_text = norm_id(text)
    for key, value in ai["fields"].items():
        if not value:
            continue
        if key in ID_FIELDS and has_text:
            if norm_id(value) in flat_text:
                fields[key] = norm_id(value) if key != "ein" else value
            elif key not in fields:
                result["notes"].append(f"AI returned a {key} that isn't printed on the document; ignored.")
        elif key not in fields or not has_text:
            fields[key] = value
    if result["doc_type"] != rule_type and has_text:
        fields.update(extract_by_rules(text, result["doc_type"]))
    if not fields.get("state") or not fields.get("pincode"):
        state, pin = _state_and_pin(fields.get("address"))
        fields.setdefault("state", state)
        fields.setdefault("pincode", pin)
    result["fields"] = {k: v for k, v in fields.items() if v}
    return result


# AI readings are cached by file content: the same file always gets the same reading, and a
# repeated demo run costs no quota. The reader label says "(cached)" whenever this is used.
_cache_lock = threading.Lock()
_cache: dict | None = None


def _cache_file() -> Path:
    return config.DATA / "ai_cache.json"


def _cache_load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_cache_file().read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    return _cache


def clear_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = {}
        _cache_file().unlink(missing_ok=True)


def read_documents(paths: list[Path], use_ai: bool = True) -> list[dict]:
    """Read several documents. All uncached files go to the AI in ONE request (kind to rate limits)."""
    readings = [_rule_reading(p) for p in paths]
    results = [r for r, _ in readings]
    if not (use_ai and llm.provider()):
        for r in results:
            if not r["has_text"]:
                r["notes"].append("No text layer (scanned image) and the AI reader is off, so it can't be read.")
        return results

    blobs = [p.read_bytes() for p in paths]
    keys = [hashlib.sha256(b).hexdigest() for b in blobs]
    with _cache_lock:
        cache = _cache_load() if config.AI_CACHE else {}
        hits = {i: cache[k] for i, k in enumerate(keys) if k in cache}
    todo = [i for i in range(len(paths)) if i not in hits]
    fresh = {}
    if todo:
        try:
            answers = llm.extract_documents([(blobs[i], _mime(paths[i]), readings[i][1][:6000]) for i in todo])
            fresh = dict(zip(todo, answers))
        except llm.AIUnavailable as e:
            for i in todo:
                results[i]["notes"].append(f"AI reader unavailable, used rules instead ({str(e)[:140]}).")
        if fresh and config.AI_CACHE:
            with _cache_lock:
                cache = _cache_load()
                cache.update({keys[i]: a for i, a in fresh.items()})
                config.DATA.mkdir(parents=True, exist_ok=True)
                _cache_file().write_text(json.dumps(cache), encoding="utf-8")
    for i in range(len(paths)):
        if i in hits:
            _merge_ai(results[i], hits[i], readings[i][1], cached=True)
        elif i in fresh:
            _merge_ai(results[i], fresh[i], readings[i][1], cached=False)
    return results


def read_document(path: Path, use_ai: bool = True) -> dict:
    """Return {doc_type, confidence, fields, reader, notes, has_text} for one document."""
    return read_documents([path], use_ai)[0]
