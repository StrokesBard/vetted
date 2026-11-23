"""Demo scenarios: one happy path, four designed edge cases, two extras.

Each scenario is a realistic vendor submission (form data + documents) plus the status we expect.
The same definitions drive the scenario launcher in the UI and the automated test suite.
All companies and identifiers are fictional.
"""
from __future__ import annotations

from .validators import make_gstin


def _in_company(name, entity, pan, state_code, addr, city, state, pin, email, contact, website,
                bank_name, ifsc, account, holder=None):
    return {
        "company": {
            "legal_name": name, "entity_type": entity, "country": "IN",
            "address": {"line1": addr, "city": city, "state": state, "pincode": pin},
            "website": website, "contact_name": contact, "contact_email": email,
        },
        "tax": {"pan": pan, "gstin": make_gstin(state_code, pan)},
        "bank": {"account_holder": holder or name, "account_number": account, "ifsc": ifsc, "bank_name": bank_name},
    }


def _addr_lines(sub):
    a = sub["company"]["address"]
    return [a["line1"], f"{a['city']}, {a['state']}, {a['pincode']}"]


def _in_docs(sub, *, constitution, id_label, id_value, tan, branch, trade_name=None,
             cert_name=None, cheque_holder=None, company_account=True, gst_overrides=None):
    c, t, b = sub["company"], sub["tax"], sub["bank"]
    gst_args = {
        "legal_name": cert_name or c["legal_name"], "trade_name": trade_name or c["legal_name"],
        "gstin": t["gstin"], "constitution": constitution, "address_lines": _addr_lines(sub),
    }
    gst_args.update(gst_overrides or {})
    return [
        {"slot": "tax_certificate", "file": "gst_certificate.pdf", "kind": "gst_certificate", "args": gst_args},
        {"slot": "bank_proof", "file": "cancelled_cheque.pdf", "kind": "cancelled_cheque", "args": {
            "bank_name": b["bank_name"], "branch": branch, "ifsc": b["ifsc"],
            "account_number": b["account_number"], "holder": cheque_holder or c["legal_name"],
            "company_account": company_account}},
        {"slot": "incorporation", "file": "certificate_of_incorporation.pdf", "kind": "incorporation_certificate",
         "args": {"legal_name": c["legal_name"], "id_label": id_label, "id_value": id_value,
                  "pan": t["pan"], "tan": tan, "address_lines": _addr_lines(sub)}},
    ]


CIN = "Corporate Identity Number (CIN)"
PVT = "Private Limited Company"

# ---------------------------------------------------------------- happy path
_sharma = _in_company(
    "Sharma Logistics Private Limited", "private_limited", "AAKCS4821M", "29",
    "14, 2nd Floor, MG Road", "Bengaluru", "Karnataka", "560001",
    "accounts@sharmalogistics.in", "Anita Sharma", "sharmalogistics.in",
    "HDFC Bank", "HDFC0001234", "50200012345678")

# ------------------------------------------------- EC1: GST state vs address
_nilgiri = _in_company(
    "Nilgiri Packaging Private Limited", "private_limited", "AAFCN7719K", "27",
    "88, Industrial Layout, Peenya", "Bengaluru", "Karnataka", "560058",
    "finance@nilgiripack.com", "Karthik Rao", "nilgiripack.com",
    "ICICI Bank", "ICIC0000456", "004505012876")
_nilgiri_mh_addr = ["Plot 21, MIDC Bhosari", "Pune, Maharashtra, 411026"]
_nilgiri_ka_gstin = make_gstin("29", "AAFCN7719K")

# ---------------------------------------------- EC2: wrong document uploaded
_deccan = _in_company(
    "Deccan Print Works LLP", "llp", "AAQFD3344P", "36",
    "5-9-22, Secretariat Road, Saifabad", "Hyderabad", "Telangana", "500004",
    "billing@deccanprint.in", "Farah Qureshi", "deccanprint.in",
    "State Bank of India", "SBIN0020087", "38211456720")

# ------------------------------------------------ EC3: bank account reuse
_orbit = _in_company(
    "Orbit Facility Services Private Limited", "private_limited", "AAHCO5512R", "06",
    "Unit 407, Sector 44", "Gurugram", "Haryana", "122003",
    "orbit.facility.services@gmail.com", "Vikas Mehta", "",
    "Axis Bank", "UTIB0002931", "917020044517733")

# --------------------------------------------- EC4: looks wrong, is fine
_malabar = _in_company(
    "Malabar Spice Exports Private Limited", "private_limited", "AAECM9087L", "32",
    "TC 12/340, Bazaar Road, Mattancherry", "Kochi", "Kerala", "682002",
    "ap@malabarspice.co.in", "Jacob Thomas", "malabarspice.co.in",
    "Federal Bank", "FDRL0001109", "11090200034512")

# ----------------------------------------------------- extra: documents only
_konkan = _in_company(
    "Konkan Coastal Foods Private Limited", "private_limited", "AABCK2203H", "27",
    "301, Shreeji Chambers, Opera House", "Mumbai", "Maharashtra", "400004",
    "accounts@konkanfoods.in", "Sneha Patil", "konkanfoods.in",
    "Kotak Mahindra Bank", "KKBK0000958", "6712008834")

# ---------------------------------------------------------- extra: US vendor
_bright_addr = ["2100 Rio Grande Street, Suite 300", "Austin, TX 78705"]
_brightline = {
    "company": {
        "legal_name": "Brightline Analytics Inc", "entity_type": "corporation", "country": "US",
        "address": {"line1": "2100 Rio Grande Street, Suite 300", "city": "Austin", "state": "Texas", "pincode": "78705"},
        "website": "brightlineanalytics.com", "contact_name": "Dana Whitfield",
        "contact_email": "ap@brightlineanalytics.com",
    },
    "tax": {"ein": "47-2918365"},
    "bank": {"account_holder": "Brightline Analytics Inc", "account_number": "483920175566",
             "routing_number": "021000021", "bank_name": "JPMorgan Chase Bank"},
}


SCENARIOS = {
    "clean": {
        "title": "Clean vendor",
        "kind": "Happy path",
        "expect": "approved",
        "blurb": "Everything present, valid and consistent across the form and all three documents.",
        "channel": "portal",
        "submission": _sharma,
        "docs": _in_docs(_sharma, constitution=PVT, id_label=CIN, id_value="U63090KA2018PTC112233",
                         tan="BLRS12345A", branch="MG Road, Bengaluru", trade_name="Sharma Logistics"),
    },
    "state_mismatch": {
        "title": "Subtle mismatch",
        "kind": "Edge case 1",
        "expect": "pending",
        "expect_after_fix": "approved",
        "blurb": "Every field is well-formed and the GSTIN checksum is valid, but the GST registration is for "
                 "Maharashtra while the registered address is in Karnataka.",
        "channel": "portal",
        "submission": _nilgiri,
        "docs": _in_docs(_nilgiri, constitution=PVT, id_label=CIN, id_value="U21000KA2016PTC098812",
                         tan="BLRN09876C", branch="Peenya, Bengaluru", trade_name="Nilgiri Packaging",
                         gst_overrides={"address_lines": _nilgiri_mh_addr}),
        "fix": {"fields": {"tax.gstin": _nilgiri_ka_gstin},
                "docs": [{"slot": "tax_certificate", "file": "fix_gst_certificate_karnataka.pdf"}]},
        "fix_docs": [{"slot": "tax_certificate", "file": "fix_gst_certificate_karnataka.pdf", "kind": "gst_certificate",
                      "args": {"legal_name": "Nilgiri Packaging Private Limited", "trade_name": "Nilgiri Packaging",
                               "gstin": _nilgiri_ka_gstin, "constitution": PVT,
                               "address_lines": _addr_lines(_nilgiri), "valid_from": "12/09/2019"}}],
    },
    "wrong_document": {
        "title": "Wrong document",
        "kind": "Edge case 2",
        "expect": "pending",
        "expect_after_fix": "approved",
        "blurb": "The file uploaded as bank proof is actually an invoice. The vendor gets a precise request and "
                 "a link to fix only that.",
        "channel": "portal",
        "submission": _deccan,
        "docs": [d for d in _in_docs(_deccan, constitution="Limited Liability Partnership",
                                     id_label="LLP Identification Number (LLPIN)", id_value="AAK-4471",
                                     tan="HYDD04521B", branch="Saifabad, Hyderabad", trade_name="Deccan Print Works")
                 if d["slot"] != "bank_proof"] + [
            {"slot": "bank_proof", "file": "cancelled_cheque.pdf", "kind": "invoice", "args": {
                "seller": "Deccan Print Works LLP", "seller_gstin": _deccan["tax"]["gstin"],
                "buyer": "Acme Retail India Private Limited"}}],
        "fix": {"fields": {}, "docs": [{"slot": "bank_proof", "file": "fix_cancelled_cheque.pdf"}]},
        "fix_docs": [{"slot": "bank_proof", "file": "fix_cancelled_cheque.pdf", "kind": "cancelled_cheque", "args": {
            "bank_name": "State Bank of India", "branch": "Saifabad, Hyderabad", "ifsc": "SBIN0020087",
            "account_number": "38211456720", "holder": "Deccan Print Works LLP"}}],
    },
    "bank_reuse": {
        "title": "Bank account reuse",
        "kind": "Edge case 3",
        "expect": "rejected",
        "blurb": "Looks complete, but the bank account already belongs to another vendor and is held in a "
                 "person's name. Rejected, escalated, and the vendor is not told why.",
        "channel": "portal",
        "submission": _orbit,
        "docs": _in_docs(_orbit, constitution=PVT, id_label=CIN, id_value="U74900HR2021PTC094410",
                         tan="RTKO03311F", branch="Sector 44, Gurugram", trade_name="Orbit Facility Services",
                         cheque_holder="Ravi Kumar", company_account=False),
    },
    "name_variants": {
        "title": "Looks wrong, is fine",
        "kind": "Edge case 4",
        "expect": "approved",
        "blurb": "Names are written three different ways (Private Limited, PVT. LTD., a brand name as trade name). "
                 "The process recognises them as the same entity instead of raising a false alarm.",
        "channel": "portal",
        "submission": _malabar,
        "docs": _in_docs(_malabar, constitution=PVT, id_label=CIN, id_value="U15490KL2012PTC031877",
                         tan="CHNM06620D", branch="Mattancherry, Kochi", trade_name="SpiceRoute",
                         cert_name="MALABAR SPICE EXPORTS PRIVATE LIMITED",
                         cheque_holder="MALABAR SPICE EXPORTS PVT. LTD."),
    },
    "documents_only": {
        "title": "Pile of PDFs, no form",
        "kind": "Extra",
        "expect": "approved",
        "blurb": "The vendor emailed three attachments and never filled the form. The process works out which "
                 "document is which, builds the form itself, then runs the same checks.",
        "channel": "documents_only",
        "submission": {"company": {"contact_email": _konkan["company"]["contact_email"], "country": "IN"}},
        "docs": [dict(d, slot=None) for d in _in_docs(
            _konkan, constitution=PVT, id_label=CIN, id_value="U15122MH2015PTC267301",
            tan="MUMK11872E", branch="Opera House, Mumbai", trade_name="Konkan Coastal Foods")],
    },
    "us_vendor": {
        "title": "US vendor",
        "kind": "Extra",
        "expect": "approved",
        "blurb": "A different country means a different rulebook: EIN instead of GSTIN, ABA routing checksum "
                 "instead of IFSC, an IRS letter instead of a GST certificate.",
        "channel": "portal",
        "submission": _brightline,
        "docs": [
            {"slot": "tax_certificate", "file": "irs_ein_letter.pdf", "kind": "irs_ein_letter", "args": {
                "legal_name": "Brightline Analytics Inc", "ein": "47-2918365", "address_lines": _bright_addr}},
            {"slot": "bank_proof", "file": "bank_letter.pdf", "kind": "us_bank_letter", "args": {
                "bank_name": "JPMorgan Chase Bank", "holder": "Brightline Analytics Inc", "routing": "021000021",
                "account_number": "483920175566", "address_lines": _bright_addr}},
            {"slot": "incorporation", "file": "certificate_of_incorporation.pdf", "kind": "us_incorporation", "args": {
                "legal_name": "Brightline Analytics Inc", "file_number": "5284471", "state_name": "Delaware",
                "address_lines": _bright_addr}},
        ],
    },
}

# Vendors that already exist in the company's vendor master (used for duplicate and fraud checks).
SEED_VENDORS = [
    {"code": "VM-0011", "legal_name": "Aravali Steel Traders Private Limited", "pan": "AACCA1180B",
     "tax_id": make_gstin("08", "AACCA1180B"), "bank_account": "920020031177452", "bank_code": "UTIB0000712"},
    {"code": "VM-0014", "legal_name": "Coromandel Office Supplies LLP", "pan": "AAJFC6031E",
     "tax_id": make_gstin("33", "AAJFC6031E"), "bank_account": "10044781235", "bank_code": "SBIN0001821"},
    {"code": "VM-0017", "legal_name": "Kumar Traders", "pan": "BXKPK7745Q",
     "tax_id": make_gstin("06", "BXKPK7745Q"), "bank_account": "917020044517733", "bank_code": "UTIB0002931"},
    {"code": "VM-0021", "legal_name": "Sahyadri Cold Chain Private Limited", "pan": "AAMCS9902J",
     "tax_id": make_gstin("27", "AAMCS9902J"), "bank_account": "50100288841209", "bank_code": "HDFC0000542"},
    {"code": "VM-0026", "legal_name": "Tidewater Software Inc", "pan": "",
     "tax_id": "84-3310927", "bank_account": "229004417803", "bank_code": "026009593"},
]

# Names procurement has blocked (stand-in for a sanctions / debarment list).
BLOCKLIST = ["Zenith Shell Trading", "Golden Harbour Imports", "Northwind Phantom Supplies"]
