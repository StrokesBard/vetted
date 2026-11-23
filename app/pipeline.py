"""The onboarding process: eight stages from raw submission to a reasoned decision.

Design rule: AI reads documents and drafts emails. Plain code runs every check and makes the
decision, so the same input always produces the same, explainable outcome.

Severity of a finding:
  pass   check passed
  note   worth a reviewer's eye, does not hold up approval
  fix    the vendor can fix this -> Pending
  block  evidence of a fraud / identity problem -> Rejected and escalated
"""
from __future__ import annotations

import copy
import re
import time
from pathlib import Path

from . import messages, validators as v
from .docreader import DOC_LABELS, read_documents, reader_label

STAGES = [
    ("intake", "Intake"),
    ("documents", "Read documents"),
    ("completeness", "Completeness"),
    ("formats", "Validate formats"),
    ("cross_check", "Cross-check"),
    ("risk", "Risk screen"),
    ("decision", "Decide"),
    ("messages", "Draft messages"),
]

SLOTS = {
    "tax_certificate": "tax registration certificate",
    "bank_proof": "bank proof",
    "incorporation": "incorporation certificate",
}

ACCEPTED = {
    "IN": {"tax_certificate": ["gst_certificate"], "bank_proof": ["cancelled_cheque", "bank_letter"],
           "incorporation": ["incorporation_certificate"]},
    "US": {"tax_certificate": ["irs_ein_letter"], "bank_proof": ["bank_letter", "cancelled_cheque"],
           "incorporation": ["incorporation_certificate"]},
}

SLOT_ASK = {
    "IN": {"tax_certificate": "your GST registration certificate (Form GST REG-06)",
           "bank_proof": "a cancelled cheque or bank letter showing the account holder name, account number and IFSC",
           "incorporation": "your certificate of incorporation"},
    "US": {"tax_certificate": "your IRS EIN confirmation letter (CP 575 or 147C)",
           "bank_proof": "a bank letter or voided check showing the account holder name, routing number and account number",
           "incorporation": "your certificate of incorporation"},
}

REQUIRED = {
    "IN": [("company.legal_name", "Legal name"), ("company.entity_type", "Entity type"),
           ("company.address.line1", "Address"), ("company.address.city", "City"),
           ("company.address.state", "State"), ("company.address.pincode", "PIN code"),
           ("company.contact_email", "Contact email"), ("tax.pan", "PAN"), ("tax.gstin", "GSTIN"),
           ("bank.account_holder", "Bank account holder"), ("bank.account_number", "Bank account number"),
           ("bank.ifsc", "IFSC")],
    "US": [("company.legal_name", "Legal name"), ("company.entity_type", "Entity type"),
           ("company.address.line1", "Address"), ("company.address.city", "City"),
           ("company.address.state", "State"), ("company.address.pincode", "ZIP code"),
           ("company.contact_email", "Contact email"), ("tax.ein", "EIN"),
           ("bank.account_holder", "Bank account holder"), ("bank.account_number", "Bank account number"),
           ("bank.routing_number", "ABA routing number")],
}

MATRIX_ROWS = {
    "IN": [("legal_name", "Legal name"), ("pan", "PAN"), ("gstin", "GSTIN"), ("state", "State"),
           ("account_holder", "Bank account holder"), ("account_number", "Account number"), ("ifsc", "IFSC")],
    "US": [("legal_name", "Legal name"), ("ein", "EIN"), ("state", "State"),
           ("account_holder", "Bank account holder"), ("account_number", "Account number"),
           ("routing_number", "Routing number")],
}
MATRIX_COLS = [("form", "Form"), ("tax_certificate", "Tax certificate"), ("bank_proof", "Bank proof"),
               ("incorporation", "Incorporation cert")]

# The rule catalogue shown on the "How it works" page.
CHECKS = [
    ("C1", "completeness", "Required fields present", "fix"),
    ("C2", "completeness", "Required documents present", "fix"),
    ("C3", "documents", "Each document is the type its slot requires", "fix"),
    ("C4", "documents", "Document is readable with enough confidence", "fix"),
    ("C5", "formats", "PAN format", "fix"),
    ("C6", "formats", "PAN holder type matches the entity type", "fix"),
    ("C7", "formats", "GSTIN format and check digit", "fix"),
    ("C8", "formats", "PAN inside the GSTIN equals the PAN provided", "fix"),
    ("C9", "cross_check", "GST registration state equals the address state", "fix"),
    ("C10", "formats", "IFSC format, and bank matches the bank proof", "fix"),
    ("C11", "formats", "US: EIN format and ABA routing checksum", "fix"),
    ("C12", "formats", "Tax ID type matches the claimed country", "fix"),
    ("C13", "cross_check", "Legal name agrees across form and documents", "note / fix"),
    ("C14", "cross_check", "Bank account holder is the vendor itself", "fix / block"),
    ("C15", "risk", "Bank account not already used by another vendor", "block"),
    ("C16", "risk", "Tax ID not already registered", "fix / block"),
    ("C17", "risk", "Contact email is on a company domain", "note"),
    ("C18", "risk", "Vendor is not on the blocked list", "block"),
    ("C19", "formats", "Email, PIN/ZIP and account number formats", "fix"),
    ("C20", "cross_check", "IDs and bank details on documents equal the form", "fix"),
]


def get(d: dict, path: str, default=""):
    for part in path.split("."):
        d = d.get(part) if isinstance(d, dict) else None
        if d is None:
            return default
    return d if d != "" else default


def put(d: dict, path: str, value) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def _an(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


class Pipeline:
    def __init__(self, submission: dict, files: list[dict], *, channel="portal", vendors=(), blocklist=(),
                 emit=None, delay=0.0, use_ai=True, fix_link=""):
        self.sub = copy.deepcopy(submission)
        self.files = files                # [{"slot": str | None, "path": Path, "name": str}]
        self.channel = channel
        self.vendors = list(vendors)
        self.blocklist = list(blocklist)
        self._emit = emit or (lambda e: None)
        self.delay = delay
        self.use_ai = use_ai
        self.fix_link = fix_link
        self.findings: list[dict] = []
        self.stages: list[dict] = []
        self.readings: dict[str, dict] = {}   # every uploaded file, by slot
        self.docs: dict[str, dict] = {}       # only documents that are usable for cross-checks
        self.extra_files: list[dict] = []
        self.cells: dict[tuple, dict] = {}
        self.autofilled: list[str] = []
        self.country = (get(self.sub, "company.country") or "IN").upper()
        self._stage = ""

    # ------------------------------------------------------------ plumbing

    def add(self, check, severity, title, detail="", *, vendor_text=None, asks=None, source=None,
            internal_only=False):
        f = {"check": check, "stage": self._stage, "severity": severity, "title": title, "detail": detail,
             "vendor_text": vendor_text, "asks": asks or [], "source": source, "internal_only": internal_only}
        self.findings.append(f)
        self._emit({"type": "finding", "finding": f})
        return f

    def cell(self, row, col, value, status="ok", hl=None):
        self.cells[(row, col)] = {"value": value or "", "status": status if value else "na",
                                  "hl": hl if hl is not None else value}

    def _run_stage(self, key, label, fn):
        self._stage = key
        self._emit({"type": "stage_start", "stage": key, "label": label})
        before = len(self.findings)
        t0 = time.perf_counter()
        summary = fn()
        work_ms = int((time.perf_counter() - t0) * 1000)
        if self.delay > work_ms / 1000:
            time.sleep(self.delay - work_ms / 1000)   # pacing only, so a person can follow the live view
        mine = self.findings[before:]
        worst = next((s for s in ("block", "fix", "note") if any(f["severity"] == s for f in mine)), "pass")
        stage = {"key": key, "label": label, "ms": work_ms, "summary": summary, "worst": worst,
                 "findings": len(mine)}
        self.stages.append(stage)
        self._emit({"type": "stage_done", **stage})

    def run(self) -> dict:
        t0 = time.perf_counter()
        for key, label in STAGES:
            self._run_stage(key, label, getattr(self, f"stage_{key}"))
        result = {
            "submission": self.sub, "channel": self.channel, "country": self.country,
            "findings": self.findings, "stages": self.stages, "matrix": self._matrix(),
            "documents": self.readings, "extra_files": self.extra_files, "autofilled": self.autofilled,
            "status": self.status, "verdict": self.verdict, "top_reason": self.top_reason,
            "asks": self._asks(), "vendor_message": self.vendor_msg, "internal_note": self.note,
            "work_ms": sum(s["ms"] for s in self.stages),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
        self._emit({"type": "done", "status": self.status, "verdict": self.verdict})
        return result

    # --------------------------------------------------------- 1. intake

    def stage_intake(self):
        n = len(self.files)
        how = "vendor portal form" if self.channel == "portal" else "email intake, no form"
        self.add("C0", "pass", f"Submission received with {n} document{'s' if n != 1 else ''}", f"Channel: {how}.")
        return f"{n} document{'s' if n != 1 else ''} via {how}"

    # ------------------------------------------------- 2. read documents

    def stage_documents(self):
        # One call reads every document (a single AI request per case when the AI reader is on).
        results = read_documents([Path(f["path"]) for f in self.files], use_ai=self.use_ai)
        reads = []
        for f, r in zip(self.files, results):
            r["original_name"] = f["name"]
            reads.append((f, r))

        if self.channel == "documents_only" and not get(self.sub, "company.country"):
            self.country = "US" if any(r["doc_type"] == "irs_ein_letter" for _, r in reads) else "IN"
            put(self.sub, "company.country", self.country)
        accepted = ACCEPTED[self.country]

        for f, r in reads:
            label = DOC_LABELS.get(r["doc_type"], r["doc_type"])
            how = f"Read by {reader_label(r['reader'])} · {int(r['confidence'] * 100)}% confident."
            if r["notes"]:
                how += " " + " ".join(r["notes"])
            slot = f["slot"]

            if slot is None:   # unsorted upload: work out which document this is
                slot = next((s for s, types in accepted.items()
                             if r["doc_type"] in types and s not in self.readings), None)
                if slot is None:
                    self.extra_files.append(r)
                    self.add("C3", "note", f"Set aside {f['name']}: {_an(label)} isn't one of the required documents", how)
                    continue
                f["slot"] = slot
                self.readings[slot] = r
                self.docs[slot] = r
                self.add("C3", "pass", f"Recognised {f['name']} as the {SLOTS[slot]}", f"It is {_an(label)}. {how}")
                continue

            self.readings[slot] = r
            wanted = SLOT_ASK[self.country][slot]
            ask = [{"kind": "document", "slot": slot, "label": wanted[0].upper() + wanted[1:]}]
            if r["doc_type"] == "unreadable":
                self.add("C4", "fix", f"The {SLOTS[slot]} couldn't be read", how,
                         vendor_text=f"We couldn't read the file you uploaded as {SLOTS[slot]}. Please upload a clear copy of {wanted}.",
                         asks=ask)
            elif r["doc_type"] not in accepted[slot]:
                self.add("C3", "fix", f"The file uploaded as {SLOTS[slot]} is {_an(label)}, not what was asked for",
                         f"Expected {wanted}. {how}",
                         vendor_text=f"The file you uploaded as {SLOTS[slot]} looks like {_an(label)}. Please upload {wanted}.",
                         asks=ask, source={"slot": slot, "value": ""})
            elif r["confidence"] < 0.6:
                self.add("C4", "fix", f"The {SLOTS[slot]} is too unclear to rely on", how,
                         vendor_text=f"The {SLOTS[slot]} you uploaded is hard to read. Please upload a clearer copy of {wanted}.",
                         asks=ask)
            else:
                self.docs[slot] = r
                self.add("C3", "pass", f"{SLOTS[slot].capitalize()} is {_an(label)}, as required", how)

        if self.channel == "documents_only":
            self._build_form_from_documents()
        ai = [r for _, r in reads if r["reader"].startswith("ai")]
        if not ai:
            how_read = "rule-based reader"
        elif all("(cached)" in r["reader"] for r in ai):
            how_read = "AI reader, reused earlier readings"
        else:
            how_read = "AI reader"
        return f"{len(self.docs)} of {len(reads)} usable · {how_read}"

    def _build_form_from_documents(self):
        """No form was filled. Derive it from what the documents say, recording where each value came from."""
        t = self.docs.get("tax_certificate", {}).get("fields", {})
        b = self.docs.get("bank_proof", {}).get("fields", {})
        i = self.docs.get("incorporation", {}).get("fields", {})

        def fill(path, value, origin):
            if value and not get(self.sub, path):
                put(self.sub, path, value)
                self.autofilled.append(f"{path} from {origin}")

        name = i.get("legal_name") or t.get("legal_name")
        fill("company.legal_name", name.title() if name and name.isupper() else name, "incorporation certificate")
        constitution = (t.get("constitution") or "").lower()
        _, form = v.split_name(name or "")
        entity = ("llp" if "liability partnership" in constitution or form == "llp"
                  else "private_limited" if "private" in constitution or form == "private_limited"
                  else "proprietorship" if "propriet" in constitution
                  else "partnership" if "partnership" in constitution
                  else "corporation" if form in ("inc", "corp") else "llc" if form == "llc"
                  else "public_limited" if form == "limited" else "")
        fill("company.entity_type", entity, "tax certificate")
        address = i.get("address") or t.get("address") or ""
        parts = [p.strip() for p in address.split(",") if p.strip()]
        state = i.get("state") or t.get("state")
        pin = i.get("pincode") or t.get("pincode")
        tail = 3 if self.country == "IN" else 2
        if len(parts) > tail:
            fill("company.address.line1", ", ".join(parts[:-tail]), "incorporation certificate")
            fill("company.address.city", parts[-tail], "incorporation certificate")
        fill("company.address.state", state, "incorporation certificate")
        fill("company.address.pincode", pin, "incorporation certificate")
        fill("tax.pan", i.get("pan") or v.gstin_pan(t.get("gstin", "")), "incorporation certificate")
        fill("tax.gstin", t.get("gstin"), "tax certificate")
        fill("tax.ein", t.get("ein"), "tax certificate")
        holder = b.get("account_holder")
        fill("bank.account_holder", holder.title() if holder and holder.isupper() else holder, "bank proof")
        fill("bank.account_number", b.get("account_number"), "bank proof")
        fill("bank.ifsc", b.get("ifsc"), "bank proof")
        fill("bank.routing_number", b.get("routing_number"), "bank proof")
        fill("bank.bank_name", b.get("bank_name"), "bank proof")
        self.add("C0", "note", f"No form was submitted, so {len(self.autofilled)} fields were filled in from the documents",
                 "Each value is still cross-checked between documents below. " + "; ".join(self.autofilled) + ".")

    # ---------------------------------------------------- 3. completeness

    def stage_completeness(self):
        required = REQUIRED[self.country]
        missing = [(p, label) for p, label in required if not str(get(self.sub, p)).strip()]
        for path, label in missing:
            self.add("C1", "fix", f"Missing from the form: {label}", f"Field {path} is empty.",
                     vendor_text=f"Please provide your {label[0].lower() + label[1:] if not label.isupper() else label}.",
                     asks=[{"kind": "field", "path": path, "label": label}])
        if not missing:
            self.add("C1", "pass", f"All {len(required)} required fields are present")
        absent = [s for s in SLOTS if s not in self.readings]
        for slot in absent:
            wanted = SLOT_ASK[self.country][slot]
            self.add("C2", "fix", f"Missing document: {SLOTS[slot]}", "Nothing was uploaded for this slot.",
                     vendor_text=f"Please upload {wanted}.",
                     asks=[{"kind": "document", "slot": slot, "label": wanted[0].upper() + wanted[1:]}])
        if not absent:
            self.add("C2", "pass", "All 3 required documents were uploaded")
        return (f"{len(required) - len(missing)} of {len(required)} fields · "
                f"{len(SLOTS) - len(absent)} of {len(SLOTS)} documents")

    # ------------------------------------------------- 4. validate formats

    def stage_formats(self):
        before = len(self.findings)
        (self._formats_in if self.country == "IN" else self._formats_us)()
        email = get(self.sub, "company.contact_email")
        if email and not v.email_valid(email):
            self.add("C19", "fix", "Contact email address isn't valid", f"Got: {email}",
                     vendor_text="The contact email address doesn't look valid. Please provide a working email address.",
                     asks=[{"kind": "field", "path": "company.contact_email", "label": "Contact email"}])
        issues = sum(f["severity"] in ("fix", "block") for f in self.findings[before:])
        return f"{len(self.findings) - before} checks · {issues} issue{'s' if issues != 1 else ''}"

    def _formats_in(self):
        pan = v.norm_id(get(self.sub, "tax.pan"))
        gstin = v.norm_id(get(self.sub, "tax.gstin"))
        ifsc = v.norm_id(get(self.sub, "bank.ifsc"))
        acct = v.norm_id(get(self.sub, "bank.account_number"))
        entity = get(self.sub, "company.entity_type")
        name = get(self.sub, "company.legal_name")
        pan_field = [{"kind": "field", "path": "tax.pan", "label": "PAN of the company"}]
        gstin_field = [{"kind": "field", "path": "tax.gstin", "label": "GSTIN"}]

        if pan:
            if not v.pan_valid(pan):
                self.add("C5", "fix", "PAN isn't in a valid format",
                         f"A PAN is 5 letters, 4 digits, 1 letter. Got: {pan}",
                         vendor_text=f"The PAN you entered ({pan}) isn't in a valid format. Please re-enter it.",
                         asks=pan_field)
            else:
                expected = v.ENTITY_PAN_LETTER.get(entity)
                holder = v.pan_holder_type(pan)
                if expected and pan[3] != expected:
                    self.add("C6", "fix",
                             f"The PAN belongs to {_an(holder or 'different kind of taxpayer')}, but the vendor is registered as {_an(v.ENTITY_LABELS.get(entity, entity).lower())}",
                             f"4th character of a PAN encodes the holder type. {pan} has '{pan[3]}' = {holder}; "
                             f"expected '{expected}' for this entity type.",
                             vendor_text=f"The PAN you provided ({pan}) is registered to {_an(holder or 'different type of taxpayer')}, not to the company. Please provide the PAN issued in the company's name.",
                             asks=pan_field)
                else:
                    self.add("C5", "pass", f"PAN is valid and issued to {_an(holder or 'taxpayer')}",
                             f"{pan}: 4th character '{pan[3]}' = {holder}.")
                if expected in ("C", "F") and name and pan[4] != name.strip()[0].upper():
                    self.add("C6", "note", "PAN's 5th letter doesn't match the first letter of the legal name",
                             f"For entities, the 5th PAN character is normally the first letter of the name. "
                             f"PAN has '{pan[4]}', name starts with '{name.strip()[0].upper()}'. Can be legitimate after a name change.")
        if gstin:
            other = v.looks_like_tax_id(get(self.sub, "tax.gstin"))
            if other == "US":
                self.add("C12", "fix", "A US EIN was entered where an Indian GSTIN is required",
                         f"Country is India but the tax ID '{get(self.sub, 'tax.gstin')}' has the EIN format.",
                         vendor_text="The tax ID you entered looks like a US EIN, but your company is registered in India. Please provide your 15-character GSTIN.",
                         asks=gstin_field)
            elif not v.gstin_format_ok(gstin):
                self.add("C7", "fix", "GSTIN isn't in a valid format",
                         f"A GSTIN is 15 characters: 2-digit state code, 10-character PAN, entity number, 'Z', check digit. Got: {gstin}",
                         vendor_text=f"The GSTIN you entered ({gstin}) isn't in a valid format. Please re-enter all 15 characters.",
                         asks=gstin_field)
            elif not v.gstin_checksum_ok(gstin):
                self.add("C7", "fix", "GSTIN check digit is wrong, so this can't be a real GSTIN",
                         f"For {gstin[:14]} the last character must be '{v.gstin_check_digit(gstin[:14])}', got '{gstin[14]}'. Usually a typing error.",
                         vendor_text=f"The GSTIN you entered ({gstin}) has a typing error in it. Please check it against your GST certificate and re-enter it.",
                         asks=gstin_field)
            else:
                self.add("C7", "pass", "GSTIN format and check digit are valid",
                         f"{gstin}: state code {gstin[:2]} = {v.gstin_state(gstin)}, check digit '{gstin[14]}' verified.")
                if v.pan_valid(pan) and v.gstin_pan(gstin) != pan:
                    self.add("C8", "fix", "The PAN inside the GSTIN doesn't match the PAN provided",
                             f"Characters 3-12 of a GSTIN are the holder's PAN. GSTIN contains {v.gstin_pan(gstin)}, form says {pan}.",
                             vendor_text=f"Your GSTIN ({gstin}) and PAN ({pan}) don't belong together: a GSTIN always contains its holder's PAN. Please check both and correct whichever is wrong.",
                             asks=pan_field + gstin_field)
                elif v.pan_valid(pan):
                    self.add("C8", "pass", "The PAN inside the GSTIN matches the PAN provided")
        if ifsc:
            if not v.ifsc_valid(ifsc):
                self.add("C10", "fix", "IFSC isn't in a valid format",
                         f"An IFSC is 4 letters, a zero, then 6 characters. Got: {ifsc}",
                         vendor_text=f"The IFSC you entered ({ifsc}) isn't valid. Please check it against your cheque.",
                         asks=[{"kind": "field", "path": "bank.ifsc", "label": "IFSC"}])
            else:
                bank = v.ifsc_bank(ifsc)
                self.add("C10", "pass", f"IFSC is valid{' (' + bank + ')' if bank else ''}", ifsc)
        if acct and not re.fullmatch(r"\d{9,18}", acct):
            self.add("C19", "fix", "Bank account number isn't valid",
                     f"Indian account numbers are 9 to 18 digits. Got: {acct}",
                     vendor_text="The bank account number you entered isn't valid. Please check it against your cheque.",
                     asks=[{"kind": "field", "path": "bank.account_number", "label": "Bank account number"}])
        pin, state = get(self.sub, "company.address.pincode"), get(self.sub, "company.address.state")
        if pin:
            pin_state = v.pincode_state(pin)
            if not re.fullmatch(r"\d{6}", pin):
                self.add("C19", "fix", "PIN code isn't valid", f"Expected 6 digits. Got: {pin}",
                         vendor_text="The PIN code you entered isn't valid. Please enter the 6-digit PIN code.",
                         asks=[{"kind": "field", "path": "company.address.pincode", "label": "PIN code"}])
            elif pin_state and state and v.norm_state(pin_state) != v.norm_state(state):
                self.add("C19", "fix", f"PIN code {pin} is in {pin_state}, but the state is given as {state}",
                         "The address contradicts itself.",
                         vendor_text=f"Your address has PIN code {pin}, which is in {pin_state}, but the state is entered as {state}. Please correct your address.",
                         asks=[{"kind": "field", "path": "company.address.state", "label": "State"},
                               {"kind": "field", "path": "company.address.pincode", "label": "PIN code"}])
            else:
                self.add("C19", "pass", "Address PIN code agrees with the state", f"{pin} = {pin_state or 'unmapped'}")

    def _formats_us(self):
        ein = get(self.sub, "tax.ein")
        routing = v.norm_id(get(self.sub, "bank.routing_number"))
        acct = v.norm_id(get(self.sub, "bank.account_number"))
        stray = get(self.sub, "tax.gstin")
        if (stray and v.looks_like_tax_id(stray) == "IN") or v.looks_like_tax_id(ein) == "IN":
            self.add("C12", "fix", "An Indian GSTIN was entered, but US vendors are identified by an EIN",
                     f"Country is United States but the tax ID '{stray or ein}' has the GSTIN format.",
                     vendor_text="The tax ID you entered looks like an Indian GSTIN, but your company is registered in the United States. Please provide your EIN (format NN-NNNNNNN).",
                     asks=[{"kind": "field", "path": "tax.ein", "label": "EIN"}])
        elif ein:
            if v.ein_valid(ein):
                self.add("C11", "pass", "EIN format is valid", ein)
            else:
                self.add("C11", "fix", "EIN isn't in a valid format", f"Expected NN-NNNNNNN with a prefix the IRS issues. Got: {ein}",
                         vendor_text=f"The EIN you entered ({ein}) isn't valid. Please enter it as shown on your IRS letter (NN-NNNNNNN).",
                         asks=[{"kind": "field", "path": "tax.ein", "label": "EIN"}])
        if routing:
            if v.aba_routing_valid(routing):
                self.add("C11", "pass", "ABA routing number passes its checksum", routing)
            else:
                self.add("C11", "fix", "ABA routing number fails its checksum, so it can't be real",
                         f"Routing numbers are 9 digits with a 3-7-1 weighted checksum. Got: {routing}",
                         vendor_text="The routing number you entered has a typing error. Please check it against your bank letter.",
                         asks=[{"kind": "field", "path": "bank.routing_number", "label": "ABA routing number"}])
        if acct and not re.fullmatch(r"\d{6,17}", acct):
            self.add("C19", "fix", "Bank account number isn't valid", f"Expected 6 to 17 digits. Got: {acct}",
                     vendor_text="The bank account number you entered isn't valid. Please check it against your bank letter.",
                     asks=[{"kind": "field", "path": "bank.account_number", "label": "Bank account number"}])
        zip_code = get(self.sub, "company.address.pincode")
        if zip_code and not re.fullmatch(r"\d{5}(-\d{4})?", zip_code):
            self.add("C19", "fix", "ZIP code isn't valid", f"Got: {zip_code}",
                     vendor_text="The ZIP code you entered isn't valid.",
                     asks=[{"kind": "field", "path": "company.address.pincode", "label": "ZIP code"}])

    # ------------------------------------------------------ 5. cross-check

    def stage_cross_check(self):
        before = len(self.findings)
        t = self.docs.get("tax_certificate", {}).get("fields", {})
        b = self.docs.get("bank_proof", {}).get("fields", {})
        i = self.docs.get("incorporation", {}).get("fields", {})
        name = get(self.sub, "company.legal_name")
        entity = get(self.sub, "company.entity_type")
        state = get(self.sub, "company.address.state")

        # Form column: the reference everything else is compared to.
        self.cell("legal_name", "form", name)
        self.cell("state", "form", state)
        self.cell("account_holder", "form", get(self.sub, "bank.account_holder"))
        self.cell("account_number", "form", get(self.sub, "bank.account_number"))
        for key, path in (("pan", "tax.pan"), ("gstin", "tax.gstin"), ("ein", "tax.ein"),
                          ("ifsc", "bank.ifsc"), ("routing_number", "bank.routing_number")):
            self.cell(key, "form", get(self.sub, path))

        for slot in SLOTS:
            if slot not in self.docs:
                self.add("C20", "note", f"Couldn't cross-check against the {SLOTS[slot]}",
                         "It is missing or not usable, so the details it would confirm are unverified.")

        # Legal name on each certificate
        self._compare_name("tax_certificate", t.get("legal_name"), name)
        self._compare_name("incorporation", i.get("legal_name"), name)
        trade = t.get("trade_name")
        if trade and name and v.compare_names(trade, name)["verdict"] in ("close", "different"):
            self.add("C13", "note", f"Trade name \"{trade}\" differs from the legal name",
                     "A GST registration can carry a separate trade (brand) name. This is allowed and is not a mismatch.",
                     source={"slot": "tax_certificate", "value": trade})

        if self.country == "IN":
            self._cross_in(t, i, state)
        else:
            self._compare_id("ein", "tax_certificate", t.get("ein"), get(self.sub, "tax.ein"), "EIN", "tax.ein")
            if t.get("state"):
                self.cell("state", "tax_certificate", t["state"],
                          "ok" if v.norm_state(t["state"]) == v.norm_state(state) else "note")
            if i.get("state"):
                # A Delaware incorporation with an office elsewhere is normal, so only the office address is compared.
                self.cell("state", "incorporation", i["state"],
                          "ok" if v.norm_state(i["state"]) == v.norm_state(state) else "note")

        # Bank proof
        if b:
            self._compare_holder(b.get("account_holder"), name, entity)
            self._compare_id("account_number", "bank_proof", b.get("account_number"),
                             get(self.sub, "bank.account_number"), "Account number", "bank.account_number")
            if self.country == "IN":
                self._compare_id("ifsc", "bank_proof", b.get("ifsc"), get(self.sub, "bank.ifsc"), "IFSC", "bank.ifsc")
                expected_bank = v.ifsc_bank(get(self.sub, "bank.ifsc"))
                printed = b.get("bank_name")
                if expected_bank and printed and expected_bank.upper() not in printed.upper():
                    self.add("C10", "fix", f"The IFSC belongs to {expected_bank}, but the bank proof is from {printed.title()}",
                             f"IFSC {get(self.sub, 'bank.ifsc')} has bank code {get(self.sub, 'bank.ifsc')[:4]} = {expected_bank}.",
                             vendor_text=f"The IFSC you entered belongs to {expected_bank}, but your bank proof is from {printed.title()}. Please correct the IFSC.",
                             asks=[{"kind": "field", "path": "bank.ifsc", "label": "IFSC"}])
            else:
                self._compare_id("routing_number", "bank_proof", b.get("routing_number"),
                                 get(self.sub, "bank.routing_number"), "Routing number", "bank.routing_number")

        mine = self.findings[before:]
        issues = sum(f["severity"] in ("fix", "block") for f in mine)
        compared = sum(1 for (row, col), c in self.cells.items() if col != "form" and c["status"] != "na")
        self._emit({"type": "matrix", "matrix": self._matrix()})   # lets the live view draw the matrix now
        return f"{compared} values compared across {len(self.docs) + 1} sources · {issues} issue{'s' if issues != 1 else ''}"

    def _cross_in(self, t, i, state):
        pan, gstin = v.norm_id(get(self.sub, "tax.pan")), v.norm_id(get(self.sub, "tax.gstin"))
        cert_gstin = v.norm_id(t.get("gstin", ""))
        self._compare_id("gstin", "tax_certificate", cert_gstin, gstin, "GSTIN", "tax.gstin")
        if cert_gstin:
            in_gstin = v.gstin_pan(cert_gstin)
            self.cell("pan", "tax_certificate", f"{in_gstin} (inside GSTIN)", "ok" if in_gstin == pan else "fix", hl=cert_gstin)
        self._compare_id("pan", "incorporation", i.get("pan"), pan, "PAN", "tax.pan")

        # C9: the state the GST registration is for vs the registered address
        reg = cert_gstin or (gstin if v.gstin_format_ok(gstin) else "")
        reg_state = v.gstin_state(reg)
        if reg_state and state:
            same = v.norm_state(reg_state) == v.norm_state(state)
            self.cell("state", "tax_certificate", f"{reg_state} (code {reg[:2]})", "ok" if same else "fix", hl=reg)
            if same:
                self.add("C9", "pass", f"GST registration and registered address are both in {state}",
                         f"GSTIN state code {reg[:2]} = {reg_state}.")
            else:
                pin = get(self.sub, "company.address.pincode")
                self.add("C9", "fix", f"GST registration is for {reg_state}, but the registered address is in {state}",
                         f"GSTIN {reg} starts with state code {reg[:2]} = {reg_state}. Form address: "
                         f"{get(self.sub, 'company.address.city')}, {state} {pin}. Certificate address: {t.get('address', 'n/a')}. "
                         f"A business can hold a separate GST registration in each state it operates in, so this is "
                         f"treated as fixable, not as fraud.",
                         vendor_text=f"Your GST registration ({reg}) is for {reg_state}, but your registered address is in {state}. "
                                     f"Please share your GST registration for {state}, or correct your address if it has changed.",
                         asks=[{"kind": "field", "path": "tax.gstin", "label": f"GSTIN for {state}"},
                               {"kind": "document", "slot": "tax_certificate", "label": f"GST registration certificate for {state}"}],
                         source={"slot": "tax_certificate", "value": reg})
        if i.get("state"):
            same = v.norm_state(i["state"]) == v.norm_state(state)
            self.cell("state", "incorporation", i["state"], "ok" if same else "note")
            if not same:
                self.add("C9", "note", f"Registered office on the incorporation certificate is in {i['state']}, form says {state}",
                         "A registered office and an operating address can differ, so this is a note only.")

    def _compare_name(self, slot, doc_name, form_name):
        if not doc_name:
            return
        r = v.compare_names(form_name, doc_name)
        label = SLOTS[slot]
        src = {"slot": slot, "value": doc_name}
        if r["verdict"] == "exact":
            self.cell("legal_name", slot, doc_name, "ok")
            self.add("C13", "pass", f"Legal name on the {label} matches the form", doc_name)
        elif r["verdict"] == "formatting":
            self.cell("legal_name", slot, doc_name, "ok")
            self.add("C13", "pass", f"Legal name on the {label} matches the form (written differently)",
                     f"\"{doc_name}\" and \"{form_name}\" differ only in case, punctuation or how the legal form is spelt.",
                     source=src)
        elif r["verdict"] == "close":
            self.cell("legal_name", slot, doc_name, "fix")
            self.add("C13", "fix", f"Legal name on the {label} is close to the form, but not the same",
                     f"Form: \"{form_name}\". Document: \"{doc_name}\". Similarity {r['score']}. A one-letter difference can be a typo or a different company.",
                     vendor_text=f"The company name on your {label} (\"{doc_name}\") is slightly different from the name you entered (\"{form_name}\"). Please confirm your exact legal name.",
                     asks=[{"kind": "field", "path": "company.legal_name", "label": "Exact legal name"}], source=src)
        else:
            self.cell("legal_name", slot, doc_name, "fix")
            wanted = SLOT_ASK[self.country][slot]
            self.add("C13", "fix", f"The {label} is in the name of a different entity",
                     f"Form: \"{form_name}\". Document: \"{doc_name}\". Similarity {r['score']}.",
                     vendor_text=f"The {label} you uploaded is in the name of \"{doc_name}\", not \"{form_name}\". Please upload {wanted} issued to your company.",
                     asks=[{"kind": "document", "slot": slot, "label": wanted[0].upper() + wanted[1:]}], source=src)

    def _compare_id(self, row, slot, doc_value, form_value, label, path):
        if not doc_value:
            return
        same = v.norm_id(doc_value) == v.norm_id(form_value)
        self.cell(row, slot, doc_value, "ok" if same else "fix")
        if same:
            self.add("C20", "pass", f"{label} on the {SLOTS[slot]} matches the form", doc_value)
        else:
            self.add("C20", "fix", f"{label} on the {SLOTS[slot]} doesn't match the form",
                     f"Form: {form_value or 'empty'}. Document: {doc_value}.",
                     vendor_text=f"The {label} you entered ({form_value}) doesn't match the one on your {SLOTS[slot]} ({doc_value}). Please correct it.",
                     asks=[{"kind": "field", "path": path, "label": label}],
                     source={"slot": slot, "value": doc_value})

    def _compare_holder(self, holder, legal_name, entity):
        if not holder:
            self.add("C14", "fix", "No account holder name could be found on the bank proof", "",
                     vendor_text="We couldn't find the account holder's name on your bank proof. Please upload a cancelled cheque or bank letter that shows it.",
                     asks=[{"kind": "document", "slot": "bank_proof", "label": "Bank proof showing the account holder name"}])
            return
        r = v.compare_names(legal_name, holder)
        src = {"slot": "bank_proof", "value": holder}
        claimed = get(self.sub, "bank.account_holder")
        claim_note = (f" The form states the holder as \"{claimed}\"."
                      if claimed and v.compare_names(claimed, holder)["verdict"] in ("close", "different") else "")
        if r["verdict"] in ("exact", "formatting"):
            self.cell("account_holder", "bank_proof", holder, "ok")
            how = "" if r["verdict"] == "exact" else " (written differently)"
            self.add("C14", "pass", f"The bank account is held in the vendor's own name{how}",
                     f"Bank proof: \"{holder}\". Legal name: \"{legal_name}\".", source=src)
        elif v.looks_like_individual(holder) and entity != "proprietorship":
            self.cell("account_holder", "bank_proof", holder, "block")
            self.add("C14", "block", "The bank account is held by an individual, not the company",
                     f"Bank proof shows the holder as \"{holder}\"; the vendor is \"{legal_name}\".{claim_note} "
                     f"Paying a company's invoices into a personal account is a common payment-diversion pattern.",
                     source=src, internal_only=True)
        else:
            self.cell("account_holder", "bank_proof", holder, "fix")
            self.add("C14", "fix", "The bank account is in the name of a different entity",
                     f"Bank proof: \"{holder}\". Legal name: \"{legal_name}\". Similarity {r['score']}.{claim_note}",
                     vendor_text=f"The bank account on your bank proof is held by \"{holder}\", not \"{legal_name}\". Please provide proof of a bank account held in your company's name.",
                     asks=[{"kind": "document", "slot": "bank_proof", "label": "Bank proof in the company's name"}], source=src)

    # ------------------------------------------------------- 6. risk screen

    def stage_risk(self):
        before = len(self.findings)
        name = get(self.sub, "company.legal_name")
        proof = self.docs.get("bank_proof", {}).get("fields", {})
        acct = v.norm_id(proof.get("account_number") or get(self.sub, "bank.account_number"))
        code = v.norm_id(proof.get("ifsc") or proof.get("routing_number")
                         or get(self.sub, "bank.ifsc") or get(self.sub, "bank.routing_number"))
        tax_id = v.norm_id(get(self.sub, "tax.gstin") or get(self.sub, "tax.ein"))
        pan = v.norm_id(get(self.sub, "tax.pan"))

        def same_entity(vendor):
            return v.compare_names(vendor["legal_name"], name)["verdict"] in ("exact", "formatting")

        shared = [x for x in self.vendors if acct and v.norm_id(x["bank_account"]) == acct
                  and v.norm_id(x["bank_code"]) == code and not same_entity(x)]
        for x in shared:
            self.add("C15", "block", f"This bank account is already on file for another vendor ({x['legal_name']})",
                     f"Account {acct} / {code} belongs to {x['legal_name']} ({x['code']}) in the vendor master. "
                     f"Two unrelated vendors sharing one bank account is a strong indicator of payment fraud.",
                     internal_only=True, source={"slot": "bank_proof", "value": acct})
        if acct and not shared:
            self.add("C15", "pass", "The bank account isn't used by any other vendor",
                     f"Checked {acct} against {len(self.vendors)} vendors in the vendor master.")

        registered = [x for x in self.vendors
                      if (tax_id and v.norm_id(x["tax_id"]) == tax_id) or (pan and v.norm_id(x.get("pan", "")) == pan)]
        for x in registered:
            if same_entity(x):
                self.add("C16", "fix", f"This vendor may already be onboarded as {x['code']}",
                         f"Same tax ID and name as {x['legal_name']} ({x['code']}). Likely a duplicate request; a reviewer should confirm before creating a second record.",
                         internal_only=True)
            else:
                self.add("C16", "block", f"This tax ID is already registered to a different vendor ({x['legal_name']})",
                         f"{tax_id or pan} is on file for {x['legal_name']} ({x['code']}).", internal_only=True)
        if (tax_id or pan) and not registered:
            self.add("C16", "pass", "The tax ID isn't registered to any existing vendor")

        blocked = next((b for b in self.blocklist if name and v.compare_names(b, name)["verdict"] in ("exact", "formatting", "close")), None)
        if blocked:
            self.add("C18", "block", "This vendor is on the blocked-vendor list", f"Matched \"{blocked}\".", internal_only=True)
        elif name:
            self.add("C18", "pass", "The vendor isn't on the blocked-vendor list", f"Checked against {len(self.blocklist)} blocked names.")

        email = get(self.sub, "company.contact_email")
        if email and v.email_valid(email):
            domain = email.rsplit("@", 1)[-1].lower()
            site = re.sub(r"^(https?://)?(www\.)?", "", get(self.sub, "company.website").lower()).strip("/")
            if v.email_is_free(email):
                self.add("C17", "note", f"Contact email is a free mailbox ({domain}), not a company domain",
                         "Common for small businesses, but worth a second look alongside other signals.")
            elif site and domain != site:
                self.add("C17", "note", f"Contact email domain ({domain}) differs from the website ({site})", "")
            else:
                self.add("C17", "pass", "Contact email is on the company's own domain", domain)
        mine = self.findings[before:]
        flags = sum(f["severity"] == "block" for f in mine)
        return f"{len(self.vendors)} vendors and {len(self.blocklist)} blocked names screened · {flags} red flag{'s' if flags != 1 else ''}"

    # ------------------------------------------------------------ 7. decide

    def stage_decision(self):
        blocks = [f for f in self.findings if f["severity"] == "block"]
        fixes = [f for f in self.findings if f["severity"] == "fix"]
        notes = [f for f in self.findings if f["severity"] == "note"]
        n_docs = len(self.docs)
        if blocks:
            self.status = "rejected"
            more = f" Plus {len(blocks) - 1} more red flag{'s' if len(blocks) > 2 else ''}." if len(blocks) > 1 else ""
            self.verdict = f"{blocks[0]['title']}.{more} Escalated to compliance."
            self.top_reason = blocks[0]["title"]
        elif fixes:
            self.status = "pending"
            n = len(fixes)
            vendor_fixable = any(f["asks"] for f in fixes)
            tail = "The vendor has been told exactly what to send." if vendor_fixable else "Needs a reviewer's decision."
            first = fixes[0]["title"]
            if first[1:2].islower():   # lower-case the first word, but leave acronyms like GST alone
                first = first[0].lower() + first[1:]
            lead = f"One thing to fix: {first}." if n == 1 else f"{n} things to fix, starting with: {first}."
            self.verdict = f"{lead} {tail}"
            self.top_reason = fixes[0]["title"]
        else:
            self.status = "approved"
            extra = f" {len(notes)} note{'s' if len(notes) > 1 else ''} for the reviewer." if notes else ""
            self.verdict = f"All details are consistent across the form and {n_docs} documents.{extra}"
            self.top_reason = "All checks passed" + (f", {len(notes)} note{'s' if len(notes) > 1 else ''}" if notes else "")
        self._emit({"type": "decision", "status": self.status, "verdict": self.verdict})
        return (f"{len(blocks)} blocking · {len(fixes)} to fix · {len(notes)} notes → {self.status}")

    # ----------------------------------------------------- 8. draft messages

    def stage_messages(self):
        self.vendor_msg = messages.vendor_message(self.status, self.sub, self.findings, self.fix_link, self.use_ai)
        self.note = messages.internal_note(self.status, self.sub, self.findings, self.verdict)
        by = "worded by AI" if self.vendor_msg["written_by"].startswith("ai:") else "fixed template"
        return f"Vendor message ({by}) and internal note drafted"

    # --------------------------------------------------------------- output

    def _asks(self):
        """What the vendor's fix page should ask for: each field or document once."""
        seen, out = set(), []
        for f in self.findings:
            if f["severity"] != "fix":
                continue
            for a in f["asks"]:
                key = (a["kind"], a.get("path") or a.get("slot"))
                if key not in seen:
                    seen.add(key)
                    current = get(self.sub, a["path"]) if a["kind"] == "field" else ""
                    out.append({**a, "why": f.get("vendor_text") or f["title"], "current": current})
        return out

    def _matrix(self):
        rows = []
        for key, label in MATRIX_ROWS[self.country]:
            cells = {col: self.cells.get((key, col), {"value": "", "status": "na", "hl": ""}) for col, _ in MATRIX_COLS}
            rows.append({"key": key, "label": label, "cells": cells})
        cols = [{"key": k, "label": ("Form (auto-filled)" if k == "form" and self.autofilled else lbl),
                 "available": k == "form" or k in self.docs} for k, lbl in MATRIX_COLS]
        return {"columns": cols, "rows": rows}


def merge_fix(previous: dict, fields: dict) -> dict:
    """Apply a vendor's corrections on top of their previous submission."""
    merged = copy.deepcopy(previous)
    for path, value in (fields or {}).items():
        if str(value).strip():
            put(merged, path, str(value).strip())
    return merged
