"""Builds the two messages a decision produces: one for the vendor, one for the internal reviewer.

They are deliberately different. The vendor gets plain instructions. The reviewer gets evidence.
When fraud is suspected, the vendor message says nothing about why, so a fraudster isn't coached.
"""
from __future__ import annotations

from . import config, llm


def _greeting(sub: dict) -> str:
    name = (sub.get("company", {}).get("contact_name") or "").split(" ")[0]
    return f"Hi {name}," if name else "Hello,"


def vendor_message(status: str, sub: dict, findings: list[dict], fix_link: str, use_ai: bool) -> dict:
    company = sub.get("company", {}).get("legal_name") or "your company"
    team = f"Vendor Onboarding Team\n{config.BUYER_NAME}"

    if status == "approved":
        text = (f"Subject: You're registered as a vendor with {config.BUYER_NAME}\n\n{_greeting(sub)}\n\n"
                f"Good news: we've verified the details and documents for {company}, and your vendor "
                f"registration is approved. You can now be issued purchase orders and paid.\n\n"
                f"If your bank or tax details change, let us know before your next invoice.\n\nThanks,\n{team}")
        return {"text": text, "written_by": "template", "items": 0}

    if status == "rejected":
        # Fixed wording, never AI-written, and never explains the reason.
        text = (f"Subject: Update on your vendor registration with {config.BUYER_NAME}\n\n{_greeting(sub)}\n\n"
                f"Thank you for submitting your details. We weren't able to complete verification of the "
                f"information provided, so we can't proceed with this registration at this time.\n\n"
                f"A member of our team may contact you if any further information is needed.\n\nRegards,\n{team}")
        return {"text": text, "written_by": "template (fixed wording for rejections)", "items": 0}

    items = [f["vendor_text"] for f in findings if f["severity"] == "fix" and f.get("vendor_text")]
    if not items:
        text = (f"Subject: Your vendor registration with {config.BUYER_NAME} is under review\n\n{_greeting(sub)}\n\n"
                f"Thanks for submitting your details. Our team is completing a manual review and will be in "
                f"touch shortly. There's nothing you need to do right now.\n\nThanks,\n{team}")
        return {"text": text, "written_by": "template", "items": 0}

    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1))
    n = len(items)
    text = (f"Subject: {n} thing{'s' if n > 1 else ''} needed to finish your vendor registration\n\n{_greeting(sub)}\n\n"
            f"Thanks for submitting the details for {company}. We've checked everything and need "
            f"{'these ' + str(n) + ' things' if n > 1 else 'one thing'} from you before we can approve it:\n\n"
            f"{numbered}\n\n"
            f"You can fix just {'these items' if n > 1 else 'this'} here, without re-entering anything else:\n{fix_link}\n\n"
            f"As soon as we have {'them' if n > 1 else 'it'}, we'll re-check automatically and confirm.\n\nThanks,\n{team}")
    written_by = "template"
    if use_ai and llm.provider():
        try:
            polished, model = llm.polish_vendor_message(text, n)
            # Guardrail: the rewrite must keep every numbered item and the fix link.
            kept_items = all(f"{i}." in polished for i in range(1, n + 1))
            if kept_items and fix_link in polished and polished.lower().startswith("subject:"):
                text, written_by = polished, f"ai:{model} (checked against the rule-built draft)"
        except llm.AIUnavailable:
            pass
    return {"text": text, "written_by": written_by, "items": n}


def internal_note(status: str, sub: dict, findings: list[dict], verdict: str) -> str:
    company = sub.get("company", {}).get("legal_name") or "Unknown vendor"
    lines = [f"{company}: {status.upper()}", verdict, ""]
    groups = [("block", "Blocking"), ("fix", "Needs fixing"), ("note", "Notes for the reviewer")]
    for sev, title in groups:
        group = [f for f in findings if f["severity"] == sev]
        if not group:
            continue
        lines.append(f"{title}:")
        for f in group:
            lines.append(f"  - {f['title']}")
            if f.get("detail"):
                lines.append(f"      Evidence: {f['detail']}")
        lines.append("")
    passed = sum(f["severity"] == "pass" for f in findings)
    lines.append(f"{passed} checks passed.")
    if status == "rejected":
        lines += ["", "Recommended action: do not pay or create this vendor. Escalate to compliance. "
                      "The vendor has been sent a neutral message that does not state the reason."]
    elif status == "pending":
        lines += ["", "Recommended action: none yet. The vendor has been asked for the items above and can "
                      "submit them through their fix link; the case re-runs automatically."]
    else:
        lines += ["", "Recommended action: create the vendor record."]
    return "\n".join(lines)
