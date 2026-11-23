"""Deterministic checks. Everything here has one right answer, so it is plain code — no AI.

Each function is pure (no database, no network) so it can be unit-tested in isolation.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# ---------------------------------------------------------------- India: PAN

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# 4th character of a PAN says what kind of taxpayer holds it.
PAN_HOLDER_TYPES = {
    "C": "company",
    "P": "individual",
    "F": "firm / LLP",
    "H": "Hindu undivided family",
    "A": "association of persons",
    "T": "trust",
    "B": "body of individuals",
    "L": "local authority",
    "J": "artificial juridical person",
    "G": "government",
}

# Which PAN holder letter we expect for each entity type on the form.
ENTITY_PAN_LETTER = {
    "private_limited": "C",
    "public_limited": "C",
    "llp": "F",
    "partnership": "F",
    "proprietorship": "P",
}

ENTITY_LABELS = {
    "private_limited": "Private limited company",
    "public_limited": "Public limited company",
    "llp": "LLP",
    "partnership": "Partnership firm",
    "proprietorship": "Sole proprietorship",
    "corporation": "Corporation (US)",
    "llc": "LLC (US)",
}


def pan_valid(pan: str) -> bool:
    return bool(PAN_RE.match((pan or "").strip().upper()))


def pan_holder_type(pan: str) -> str | None:
    pan = (pan or "").strip().upper()
    return PAN_HOLDER_TYPES.get(pan[3]) if pan_valid(pan) else None


# -------------------------------------------------------------- India: GSTIN

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "36": "Telangana", "37": "Andhra Pradesh",
}


def gstin_check_digit(first14: str) -> str:
    """Luhn mod-36 check digit used by GSTN for the 15th character."""
    factor, total = 2, 0
    for ch in reversed(first14):
        code = _B36.index(ch) * factor
        total += code // 36 + code % 36
        factor = 1 if factor == 2 else 2
    return _B36[(36 - total % 36) % 36]


def gstin_format_ok(gstin: str) -> bool:
    return bool(GSTIN_RE.match((gstin or "").strip().upper()))


def gstin_checksum_ok(gstin: str) -> bool:
    g = (gstin or "").strip().upper()
    return gstin_format_ok(g) and gstin_check_digit(g[:14]) == g[14]


def gstin_state(gstin: str) -> str | None:
    return GST_STATE_CODES.get((gstin or "")[:2])


def gstin_pan(gstin: str) -> str:
    return (gstin or "").strip().upper()[2:12]


def make_gstin(state_code: str, pan: str, entity_no: str = "1") -> str:
    """Build a checksum-valid GSTIN (used by the test-data generator)."""
    first14 = f"{state_code}{pan}{entity_no}Z"
    return first14 + gstin_check_digit(first14)


# --------------------------------------------------------------- India: IFSC

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

IFSC_BANKS = {
    "HDFC": "HDFC Bank", "ICIC": "ICICI Bank", "SBIN": "State Bank of India",
    "UTIB": "Axis Bank", "KKBK": "Kotak Mahindra Bank", "PUNB": "Punjab National Bank",
    "BARB": "Bank of Baroda", "CNRB": "Canara Bank", "IDFB": "IDFC First Bank",
    "YESB": "Yes Bank", "INDB": "IndusInd Bank", "FDRL": "Federal Bank",
}


def ifsc_valid(ifsc: str) -> bool:
    return bool(IFSC_RE.match((ifsc or "").strip().upper()))


def ifsc_bank(ifsc: str) -> str | None:
    return IFSC_BANKS.get((ifsc or "").strip().upper()[:4])


# ------------------------------------------------------------ India: pincode

# First two digits of a PIN code identify the state (simplified to the common cases).
_PIN_PREFIX_STATE = [
    (("11",), "Delhi"), (("12", "13"), "Haryana"), (("14", "15"), "Punjab"),
    (("20", "21", "22", "23", "24", "25", "26", "27", "28"), "Uttar Pradesh"),
    (("30", "31", "32", "33", "34"), "Rajasthan"), (("36", "37", "38", "39"), "Gujarat"),
    (("40", "41", "42", "43", "44"), "Maharashtra"), (("45", "46", "47", "48"), "Madhya Pradesh"),
    (("50",), "Telangana"), (("51", "52", "53"), "Andhra Pradesh"),
    (("56", "57", "58", "59"), "Karnataka"), (("60", "61", "62", "63", "64"), "Tamil Nadu"),
    (("67", "68", "69"), "Kerala"), (("70", "71", "72", "73", "74"), "West Bengal"),
    (("75", "76", "77"), "Odisha"), (("78",), "Assam"), (("80", "81", "82", "83", "84", "85"), "Bihar"),
]


def pincode_state(pin: str) -> str | None:
    pin = re.sub(r"\D", "", pin or "")
    if len(pin) != 6:
        return None
    for prefixes, state in _PIN_PREFIX_STATE:
        if pin[:2] in prefixes:
            return state
    return None


# ------------------------------------------------------------------ US rules

EIN_RE = re.compile(r"^\d{2}-\d{7}$")
# IRS never issues EINs with these two-digit prefixes.
_EIN_BAD_PREFIXES = {"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "69", "70", "78", "79", "89"}


def ein_valid(ein: str) -> bool:
    ein = (ein or "").strip()
    return bool(EIN_RE.match(ein)) and ein[:2] not in _EIN_BAD_PREFIXES


def aba_routing_valid(routing: str) -> bool:
    """ABA routing numbers carry a 3-7-1 weighted checksum."""
    r = re.sub(r"\D", "", routing or "")
    if len(r) != 9:
        return False
    d = [int(c) for c in r]
    total = 3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
    return total % 10 == 0


def looks_like_tax_id(value: str) -> str | None:
    """Tell which country's tax ID a string looks like (used for the wrong-country check)."""
    v = (value or "").strip().upper()
    if gstin_format_ok(v):
        return "IN"
    if EIN_RE.match(v):
        return "US"
    return None


# --------------------------------------------------------------------- email

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.in", "outlook.com", "hotmail.com", "rediffmail.com",
             "proton.me", "protonmail.com", "icloud.com", "aol.com"}


def email_valid(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def email_is_free(email: str) -> bool:
    return (email or "").strip().lower().rsplit("@", 1)[-1] in FREE_MAIL


# ------------------------------------------------------------- name matching

# Legal-form suffixes, grouped so "Pvt Ltd" and "Private Limited" compare as the same thing.
_SUFFIX_GROUPS = {
    "private_limited": [r"PRIVATE\s+LIMITED", r"PVT\s+LTD", r"PVT\s+LIMITED", r"PRIVATE\s+LTD", r"\(P\)\s+LTD"],
    "llp": [r"LIMITED\s+LIABILITY\s+PARTNERSHIP", r"LLP"],
    "limited": [r"LIMITED", r"LTD"],
    "inc": [r"INCORPORATED", r"INC"],
    "llc": [r"LLC", r"L\s+L\s+C"],
    "corp": [r"CORPORATION", r"CORP"],
}


def split_name(name: str) -> tuple[str, str | None]:
    """Return (core name, legal-form group). 'Sharma Logistics Pvt. Ltd.' -> ('SHARMA LOGISTICS', 'private_limited')."""
    n = re.sub(r"[.,'\"]", " ", (name or "").upper())
    n = re.sub(r"&", " AND ", n)
    n = re.sub(r"\s+", " ", n).strip()
    for group, patterns in _SUFFIX_GROUPS.items():
        for pat in patterns:
            m = re.search(rf"\s{pat}$", n)
            if m:
                return n[: m.start()].strip(), group
    return n, None


def compare_names(a: str, b: str) -> dict:
    """Compare two organisation names.

    verdict:
      exact      identical strings
      formatting same name, different punctuation / case / legal-form spelling
      close      similar but not the same (likely typo or different entity) -> ask
      different  not the same name
    """
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return {"verdict": "missing", "score": 0.0}
    if a == b:
        return {"verdict": "exact", "score": 1.0}
    core_a, form_a = split_name(a)
    core_b, form_b = split_name(b)
    score = round(SequenceMatcher(None, core_a, core_b).ratio(), 2)
    same_form = form_a == form_b or form_a is None or form_b is None
    if core_a == core_b and same_form:
        return {"verdict": "formatting", "score": 1.0}
    if score >= 0.85:
        return {"verdict": "close", "score": score}
    return {"verdict": "different", "score": score}


def looks_like_individual(name: str) -> bool:
    """A bank account in a person's name: no legal-form suffix, 1-3 words, no business words."""
    core, form = split_name(name)
    if form or not core:
        return False
    words = core.split()
    business_words = {"TRADERS", "TRADING", "ENTERPRISES", "ENTERPRISE", "SERVICES", "INDUSTRIES",
                      "SOLUTIONS", "LOGISTICS", "EXPORTS", "AND", "CO", "COMPANY", "ASSOCIATES",
                      "WORKS", "FOODS", "TECHNOLOGIES", "SYSTEMS", "GROUP", "AGENCIES", "STORES"}
    return len(words) <= 3 and not (set(words) & business_words)


def norm_id(value: str) -> str:
    """Normalise an identifier for comparison: uppercase, strip spaces and dashes."""
    return re.sub(r"[\s\-]", "", (value or "").upper())


def norm_state(value: str) -> str:
    return re.sub(r"[^a-z]", "", (value or "").lower())
