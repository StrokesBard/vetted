"""Generates the test documents (PDFs) used by the demo scenarios.

All companies, IDs and bank accounts are fictional. IDs are synthetic but pass their checksums,
so the validation logic is exercised for real.

Run:  python -m app.docgen
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

INK = (0.10, 0.11, 0.14)
GREY = (0.42, 0.44, 0.48)
RULE = (0.75, 0.77, 0.80)
NAVY = (0.09, 0.20, 0.42)
RED = (0.78, 0.16, 0.16)


class Sheet:
    """Tiny layout helper: writes lines top-to-bottom so text extraction keeps reading order."""

    def __init__(self, width=595, height=842, margin=56):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=width, height=height)
        self.w, self.h, self.m = width, height, margin
        self.y = margin

    def text(self, s, size=10.5, font="helv", color=INK, x=None, center=False, gap=6):
        s = s.replace("—", "-")
        if center:
            x = (self.w - pymupdf.get_text_length(s, fontname=font, fontsize=size)) / 2
        self.page.insert_text((x if x is not None else self.m, self.y + size), s,
                              fontsize=size, fontname=font, color=color)
        self.y += size + gap

    def wrapped(self, s, size=10.5, font="helv", color=INK, width_chars=92, gap=4):
        words, line = s.split(), ""
        for w in words:
            if len(line) + len(w) + 1 > width_chars:
                self.text(line, size, font, color, gap=gap)
                line = w
            else:
                line = f"{line} {w}".strip()
        if line:
            self.text(line, size, font, color, gap=gap)

    def space(self, pts=10):
        self.y += pts

    def rule(self, color=RULE, width=0.7):
        self.page.draw_line((self.m, self.y), (self.w - self.m, self.y), color=color, width=width)
        self.y += 10

    def frame(self, inset=28, color=NAVY, width=1.2):
        self.page.draw_rect(pymupdf.Rect(inset, inset, self.w - inset, self.h - inset), color=color, width=width)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path), garbage=3, deflate=True)
        self.doc.close()


# ------------------------------------------------------------------ India

def gst_certificate(path, *, legal_name, trade_name, gstin, constitution, address_lines, valid_from="01/07/2018"):
    s = Sheet()
    s.frame()
    s.space(8)
    s.text("Government of India", 13, "hebo", NAVY, center=True)
    s.text("Form GST REG-06", 11, "hebo", center=True)
    s.text("[See Rule 10(1)]", 9, "helv", GREY, center=True)
    s.space(6)
    s.text("Registration Certificate", 15, "hebo", center=True, gap=14)
    s.rule()
    s.text(f"Registration Number : {gstin}", 12, "hebo", gap=12)
    s.rule()
    s.text(f"1. Legal Name : {legal_name.upper()}")
    s.text(f"2. Trade Name, if any : {trade_name}")
    s.text(f"3. Constitution of Business : {constitution}")
    s.text("4. Address of Principal Place of Business :")
    for line in address_lines:
        s.text(line, x=s.m + 18)
    s.text(f"5. Date of Liability : {valid_from}")
    s.text(f"6. Period of Validity : From {valid_from} To NA")
    s.text("7. Type of Registration : Regular")
    s.text("8. Particulars of Approving Authority : Goods and Services Tax Network")
    s.space(26)
    s.rule()
    s.text("Signature Not Verified — digitally signed by DS GOODS AND SERVICES TAX NETWORK", 8.5, "helv", GREY)
    s.text("This is a system generated digitally signed Registration Certificate issued based on the", 8.5, "helv", GREY, gap=3)
    s.text("deemed approval of application.", 8.5, "helv", GREY)
    s.save(path)


def incorporation_certificate(path, *, legal_name, id_label, id_value, pan, tan, address_lines,
                              date="14th day of March Two thousand eighteen", act="Companies Act, 2013"):
    s = Sheet()
    s.frame()
    s.space(8)
    s.text("GOVERNMENT OF INDIA", 13, "hebo", NAVY, center=True)
    s.text("MINISTRY OF CORPORATE AFFAIRS", 11, "hebo", NAVY, center=True)
    s.text("Central Registration Centre", 9.5, "helv", GREY, center=True)
    s.space(8)
    s.text("Certificate of Incorporation", 16, "hebo", center=True, gap=6)
    s.text(f"[Pursuant to the {act} and rules made thereunder]", 9, "helv", GREY, center=True, gap=16)
    s.wrapped(f"I hereby certify that {legal_name.upper()} is incorporated on this {date} "
              f"under the {act}.", 11, width_chars=84, gap=5)
    s.space(10)
    s.rule()
    s.text(f"Name of the entity : {legal_name.upper()}", 11, "hebo")
    s.text(f"{id_label} : {id_value}")
    s.text(f"Permanent Account Number (PAN) : {pan}")
    s.text(f"Tax Deduction and Collection Account Number (TAN) : {tan}")
    s.text("Registered office address :")
    for line in address_lines:
        s.text(line, x=s.m + 18)
    s.space(30)
    s.rule()
    s.text("Digitally signed by the Registrar of Companies, Central Registration Centre", 8.5, "helv", GREY)
    s.text("Disclaimer: This certificate only evidences incorporation on the basis of documents filed.", 8.5, "helv", GREY)
    s.save(path)


def cancelled_cheque(path, *, bank_name, branch, ifsc, account_number, holder, company_account=True,
                     cheque_no="000142"):
    s = Sheet(width=640, height=290, margin=28)
    p = s.page
    p.draw_rect(pymupdf.Rect(10, 10, 630, 280), color=RULE, width=1, fill=(0.975, 0.98, 0.99))
    s.y = 22
    s.text(bank_name.upper(), 15, "hebo", NAVY, gap=4)
    s.text(f"Branch: {branch}", 9, "helv", GREY, gap=3)
    s.text(f"IFSC: {ifsc}", 9.5, "cour", INK, gap=10)
    p.insert_text((500, 40), "Date: __ / __ / ____", fontsize=9, fontname="helv", color=GREY)
    s.text("Pay ______________________________________________________________ or Bearer", 10, gap=10)
    s.text("Rupees ___________________________________________________________", 10, gap=12)
    s.text(f"A/c No. {account_number}", 11.5, "cour", INK, gap=10)
    p.draw_rect(pymupdf.Rect(480, 96, 612, 126), color=INK, width=0.8)
    p.insert_text((488, 116), "Rs.", fontsize=11, fontname="hebo", color=INK)
    s.y = 190
    label = f"For {holder.upper()}" if company_account else holder.upper()
    s.text(label, 9.5, "hebo", x=380, gap=22)
    s.text("Authorised Signatory" if company_account else "Please sign above", 8.5, "helv", GREY, x=380)
    s.y = 252
    s.text(f"\"{cheque_no}\"  560240003:  012345\"  31", 11, "cour", INK, x=150)
    p.insert_text((150, 210), "CANCELLED", fontsize=44, fontname="hebo", color=RED,
                  morph=(pymupdf.Point(150, 210), pymupdf.Matrix(16)))
    s.save(path)


def invoice(path, *, seller, seller_gstin, buyer, number="INV-2291"):
    s = Sheet()
    s.text("TAX INVOICE", 18, "hebo", NAVY, gap=14)
    s.text(seller, 12, "hebo")
    s.text(f"GSTIN: {seller_gstin}", 10, "cour")
    s.space(8)
    s.text(f"Invoice No: {number}        Invoice Date: 02/11/2026        Due Date: 02/12/2026", 10)
    s.rule()
    s.text(f"Bill To: {buyer}", 10.5, "hebo")
    s.space(8)
    s.text("Description                               Qty      Rate (INR)      Amount (INR)", 9.5, "cour", GREY)
    s.rule()
    for row in ["Offset printing — brochures (A4)       5,000        6.40         32,000.00",
                "Die-cut packaging sleeves              2,000       11.25         22,500.00",
                "Design and pre-press charges               1    4,500.00          4,500.00"]:
        s.text(row, 9.5, "cour")
    s.rule()
    s.text("                                            Taxable value         59,000.00", 9.5, "cour")
    s.text("                                            IGST @ 18%            10,620.00", 9.5, "cour")
    s.text("                                            Total (INR)           69,620.00", 10, "cobo")
    s.space(20)
    s.text("Payment terms: 30 days from invoice date.", 9, "helv", GREY)
    s.save(path)


# --------------------------------------------------------------------- US

def irs_ein_letter(path, *, legal_name, ein, address_lines):
    s = Sheet()
    s.text("DEPARTMENT OF THE TREASURY", 12, "hebo", NAVY, gap=3)
    s.text("INTERNAL REVENUE SERVICE", 12, "hebo", NAVY, gap=3)
    s.text("CINCINNATI  OH  45999-0023", 10, "helv", GREY, gap=16)
    s.text("Date of this notice: 03-18-2021", 10, x=360, gap=3)
    s.text(f"Employer Identification Number: {ein}", 10, "hebo", x=360, gap=3)
    s.text("Form: SS-4     Number of this notice: CP 575 A", 10, x=360, gap=18)
    s.text(legal_name.upper(), 10.5, "hebo")
    for line in address_lines:
        s.text(line.upper(), 10.5)
    s.space(14)
    s.text("WE ASSIGNED YOU AN EMPLOYER IDENTIFICATION NUMBER", 11.5, "hebo", gap=10)
    s.wrapped(f"Thank you for applying for an Employer Identification Number (EIN). We assigned you EIN {ein}. "
              "This EIN will identify you, your business accounts, tax returns, and documents, even if you have "
              "no employees. Please keep this notice in your permanent records.", width_chars=90)
    s.space(8)
    s.wrapped("When filing tax documents, payments, and related correspondence, it is very important that you use "
              "your EIN and complete name and address exactly as shown above.", width_chars=90)
    s.space(8)
    s.text("Based on the information received from you, you must file the following form: Form 1120", 10)
    s.save(path)


def us_bank_letter(path, *, bank_name, holder, routing, account_number, address_lines):
    s = Sheet()
    s.text(bank_name.upper(), 15, "hebo", NAVY, gap=4)
    s.text("Commercial Banking — Client Service", 9.5, "helv", GREY, gap=20)
    s.text("November 4, 2026", 10, gap=14)
    s.text("BANK ACCOUNT VERIFICATION LETTER", 12.5, "hebo", gap=12)
    s.text("To whom it may concern,", 10.5, gap=10)
    s.wrapped("This letter confirms that the client named below maintains a business checking account with us "
              "in good standing.", width_chars=90)
    s.space(10)
    s.rule()
    s.text(f"Account Holder Name : {holder}", 11, "hebo")
    s.text(f"ABA Routing Number : {routing}", 10.5, "cour")
    s.text(f"Account Number : {account_number}", 10.5, "cour")
    s.text("Account Type : Business Checking")
    s.text("Account Address :")
    for line in address_lines:
        s.text(line, x=s.m + 18)
    s.rule()
    s.space(10)
    s.text("Sincerely,", 10.5, gap=22)
    s.text("Relationship Manager, Commercial Banking", 10)
    s.save(path)


def us_incorporation(path, *, legal_name, file_number, state_name, address_lines):
    s = Sheet()
    s.frame()
    s.space(10)
    s.text(f"STATE OF {state_name.upper()}", 14, "hebo", NAVY, center=True)
    s.text("SECRETARY OF STATE — DIVISION OF CORPORATIONS", 10, "hebo", NAVY, center=True, gap=16)
    s.text("Certificate of Incorporation", 16, "hebo", center=True, gap=18)
    s.wrapped(f"I, the Secretary of State of the State of {state_name}, do hereby certify that "
              f"{legal_name.upper()} is duly incorporated under the laws of the State of {state_name} and is in "
              "good standing and has a legal corporate existence so far as the records of this office show.",
              11, width_chars=84, gap=5)
    s.space(12)
    s.rule()
    s.text(f"Name of the entity : {legal_name.upper()}", 11, "hebo")
    s.text(f"State File Number : {file_number}")
    s.text("Date of Incorporation : February 9, 2021")
    s.text("Principal office address :")
    for line in address_lines:
        s.text(line, x=s.m + 18)
    s.space(30)
    s.rule()
    s.text("Authentication: 204918827 — verify this certificate online at corp.delaware.gov/authver", 8.5, "helv", GREY)
    s.save(path)


GENERATORS = {
    "gst_certificate": gst_certificate,
    "incorporation_certificate": incorporation_certificate,
    "cancelled_cheque": cancelled_cheque,
    "invoice": invoice,
    "irs_ein_letter": irs_ein_letter,
    "us_bank_letter": us_bank_letter,
    "us_incorporation": us_incorporation,
}


def build_all(root: Path) -> int:
    from .scenarios import SCENARIOS

    count = 0
    for key, sc in SCENARIOS.items():
        for group in ("docs", "fix_docs"):
            for spec in sc.get(group, []):
                GENERATORS[spec["kind"]](root / key / spec["file"], **spec["args"])
                count += 1
    return count


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "data" / "scenarios"
    print(f"Generated {build_all(out)} documents in {out}")
