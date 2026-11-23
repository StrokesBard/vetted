"""AI model client: Gemini first, Groq as fallback, and a clean 'unavailable' answer otherwise.

The pipeline never depends on this module succeeding. Every caller has a rule-based fallback,
so a rate limit or an outage mid-demo degrades the run instead of breaking it.
Only the standard library is used (no SDK), which keeps deployment simple.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from . import config

DOC_TYPES = ["gst_certificate", "incorporation_certificate", "cancelled_cheque", "bank_letter",
             "irs_ein_letter", "invoice", "other"]

EXTRACT_PROMPT = """You are reading one document submitted by a vendor during supplier onboarding.

1. Classify it as exactly one of: gst_certificate, incorporation_certificate, cancelled_cheque,
   bank_letter, irs_ein_letter, invoice, other.
2. Extract the fields below exactly as printed. Use null when a field is not on the document.
   Never guess or infer a value that is not printed.

Return only JSON in this shape:
{"doc_type": "...", "confidence": 0.0-1.0,
 "fields": {"legal_name": null, "trade_name": null, "constitution": null, "gstin": null, "pan": null,
            "ein": null, "registration_id": null, "address": null, "state": null, "pincode": null,
            "bank_name": null, "account_holder": null, "account_number": null, "ifsc": null,
            "routing_number": null}}

Notes: legal_name is the registered entity the document is about (not the bank, not the government).
For a cheque, account_holder is the name printed above the signature line.
registration_id is the CIN, LLPIN or state file number on an incorporation certificate."""


class AIUnavailable(Exception):
    pass


def provider() -> str | None:
    if config.AI_MODE == "off":
        return None
    if config.GEMINI_API_KEY:
        return "gemini"
    if config.GROQ_API_KEY:
        return "groq"
    return None


def status() -> dict:
    p = provider()
    return {
        "enabled": p is not None,
        "provider": p,
        "model": {"gemini": (gemini_models() or [""])[0], "groq": config.GROQ_MODEL}.get(p),
        "fallback": "groq" if p == "gemini" and config.GROQ_API_KEY else None,
        "mode": config.AI_MODE,
    }


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=config.AI_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise AIUnavailable(f"HTTP {e.code}: {detail}") from e
    except Exception as e:  # timeouts, DNS, malformed JSON
        raise AIUnavailable(f"{type(e).__name__}: {e}") from e


def gemini_models() -> list[str]:
    """GEMINI_MODEL may list several models, tried in order. A busy model fails over to the next."""
    return [m.strip() for m in config.GEMINI_MODEL.split(",") if m.strip()]


# A model that just failed (rate limit, overload, timeout) is skipped for a short while.
_resting: dict[str, float] = {}
REST_SECONDS = 60


def _gemini(prompt: str, *, files: list[tuple[bytes, str]] | None = None, want_json=True) -> tuple[str, str]:
    errors = []
    started = time.time()
    for model in gemini_models():
        if _resting.get(model, 0) > time.time():   # failed moments ago: don't make the user wait on it again
            errors.append(f"{model}: resting after a recent failure")
            continue
        if time.time() - started > config.AI_BUDGET:   # a slow AI must never stall a run
            errors.append(f"{model}: skipped, time budget of {config.AI_BUDGET:.0f}s used up")
            continue
        try:
            return _gemini_call(model, prompt, files=files, want_json=want_json), model
        except AIUnavailable as e:
            _resting[model] = time.time() + REST_SECONDS
            errors.append(f"{model}: {e}")
    raise AIUnavailable(" | ".join(errors))


def _gemini_call(model: str, prompt: str, *, files: list[tuple[bytes, str]] | None = None, want_json=True) -> str:
    parts = []
    for n, (data, mime) in enumerate(files or [], 1):
        parts.append({"text": f"Document {n}:"})
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    parts.append({"text": prompt})
    gen = {"temperature": 0}
    if want_json:
        gen["responseMimeType"] = "application/json"
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"x-goog-api-key": config.GEMINI_API_KEY},
        {"contents": [{"parts": parts}], "generationConfig": gen},
    )
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise AIUnavailable(f"Unexpected Gemini response: {str(data)[:200]}") from e


def _groq(prompt: str, *, want_json=True) -> str:
    body = {"model": config.GROQ_MODEL, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    if want_json:
        body["response_format"] = {"type": "json_object"}
    data = _post("https://api.groq.com/openai/v1/chat/completions",
                 {"Authorization": f"Bearer {config.GROQ_API_KEY}"}, body)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise AIUnavailable(f"Unexpected Groq response: {str(data)[:200]}") from e


def _complete(prompt: str, *, files=None, text_fallback="", want_json=True) -> tuple[str, str]:
    """Try Gemini (can see the files themselves), then Groq (text only). Returns (answer, model label)."""
    errors = []
    if config.AI_MODE != "off" and config.GEMINI_API_KEY:
        try:
            return _gemini(prompt, files=files, want_json=want_json)
        except AIUnavailable as e:
            errors.append(f"gemini: {e}")
    if config.AI_MODE != "off" and config.GROQ_API_KEY and (text_fallback or not files):
        try:
            full = prompt + (f"\n\n{text_fallback}" if text_fallback else "")
            return _groq(full, want_json=want_json), config.GROQ_MODEL
        except AIUnavailable as e:
            errors.append(f"groq: {e}")
    raise AIUnavailable("; ".join(errors) or "No AI provider configured")


def _clean(item: dict, model: str) -> dict:
    doc_type = item.get("doc_type")
    if doc_type not in DOC_TYPES:
        doc_type = "other"
    fields = {k: (str(v).strip() if v not in (None, "", "null") else None)
              for k, v in (item.get("fields") or {}).items()}
    try:
        confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {"doc_type": doc_type, "confidence": confidence, "fields": fields, "model": model}


def extract_documents(docs: list[tuple[bytes, str, str]]) -> list[dict]:
    """Classify and extract several documents in one request. docs = [(bytes, mime, text layer)].

    Returns one reading per document, in order. Raises AIUnavailable if no model answers properly.
    """
    n = len(docs)
    prompt = (EXTRACT_PROMPT + f"\n\nThere are {n} documents, labelled Document 1 to Document {n}. "
              f'Return {{"documents": [...]}} with exactly {n} objects of the shape above, in the same order.')
    texts = "\n\n".join(f"Document {i} text:\n{t}" for i, (_, _, t) in enumerate(docs, 1))
    answer, model = _complete(prompt, files=[(b, m) for b, m, _ in docs], text_fallback=texts)
    try:
        items = json.loads(answer)["documents"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise AIUnavailable(f"Model did not return the expected JSON: {answer[:120]}") from e
    if not isinstance(items, list) or len(items) != n:
        raise AIUnavailable(f"Model returned {len(items) if isinstance(items, list) else 'no'} readings for {n} documents")
    return [_clean(item if isinstance(item, dict) else {}, model) for item in items]


def polish_vendor_message(draft: str, n_items: int) -> tuple[str, str]:
    """Rewrite the rule-built vendor email so it reads naturally, without changing what is asked."""
    prompt = (
        "Rewrite this email to a vendor so it is warm, clear and professional. Rules: keep the subject line; "
        f"keep exactly {n_items} numbered items and do not add, remove or change any request; keep every "
        "identifier, name and link exactly as written; plain text only; under 220 words.\n\n" + draft
    )
    answer, model = _complete(prompt, want_json=False)
    return answer.strip(), model


def probe() -> dict:
    """One tiny call so the user can confirm their key and model name work."""
    try:
        answer, model = _complete('Reply with the JSON {"ok": true}')
        return {"ok": True, "model": model, "answer": answer[:80]}
    except AIUnavailable as e:
        return {"ok": False, "error": str(e)}
