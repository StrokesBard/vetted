# Vetted — vendor onboarding that explains itself

ASA Case Study · PS-2 (Operations): *vendor onboarding, from submission to approval.*

A vendor submits company, tax and bank details plus three documents. Vetted reads the documents,
cross-checks every fact across every source, screens for fraud signals, and decides
**Approved / Pending / Rejected** with the reasoning visible. For anything not approved it drafts
exactly what the vendor needs to fix and gives them a link that asks for only that.

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --port 8765
```

Open http://localhost:8765. No API key is needed: the app starts with a sample history and seven
runnable scenarios.

Tests: `.venv/Scripts/pip install -r requirements-dev.txt` then `.venv/Scripts/python -m pytest`.

## Try it in 60 seconds

1. **New case → Clean vendor → Run.** Watch eight stages execute and approve.
2. **New case → Wrong document → Run.** Pending. Open the case, then *Open the vendor's fix page* →
   *Use the prepared fix* → *Watch the re-run*. Version 2 is approved, and the history shows both.
3. **New case → Bank account reuse → Run.** Rejected and escalated. Compare the two messages:
   the reviewer sees the evidence, the vendor is not told why.
4. **Test suite → Run all scenarios.** Expected vs actual for all seven.

## The scenarios

| Scenario | What's wrong | Outcome |
|---|---|---|
| Clean vendor (happy path) | Nothing | Approved |
| EC1 · Subtle mismatch | Valid GSTIN, but registered for Maharashtra while the address is in Karnataka | Pending → Approved after the Karnataka registration is supplied |
| EC2 · Wrong document | The "bank proof" is an invoice | Pending → Approved after the right cheque is uploaded |
| EC3 · Bank account reuse | Account is in a person's name and already belongs to another vendor | Rejected, escalated, vendor not told why |
| EC4 · Looks wrong, is fine | Name written three ways (Private Limited / PVT. LTD. / brand trade name) | Approved, no false alarm |
| Extra · Pile of PDFs | No form, three unsorted attachments | Form is built from the documents → Approved |
| Extra · US vendor | Different country, different rulebook (EIN, ABA routing, IRS letter) | Approved |

## How it works

```
Intake → Read documents → Completeness → Validate formats → Cross-check → Risk screen → Decide → Draft messages
            (AI + rules)                                                                            (AI + rules)
```

**AI reads. Rules decide.** A model is used to classify documents, read scans and odd layouts, and
word the email to the vendor. Every check with one right answer, and the decision itself, is plain
code — so the same input always gives the same, explainable result.

- **Guardrail:** an identifier the AI returns must literally appear in the document's text layer,
  or it is discarded.
- **Fallback:** if no key is set, or the model is rate-limited mid-run, the rule-based reader takes
  over. The run degrades instead of breaking.
- **Rejected** is reserved for evidence of fraud or false identity. Anything the vendor could fix
  is **Pending**.
- **Two messages:** plain instructions for the vendor, evidence for the reviewer. Rejection wording
  is fixed and never AI-written.
- **People stay in charge:** a reviewer can override any decision with a reason; it is audit-logged.

## Optional: turn on the AI reader (free)

```bash
cp .env.example .env      # then paste a key into .env
```

- Gemini (free tier): https://aistudio.google.com → *Get API key* → `GEMINI_API_KEY=`
- Groq (free tier, used as a fallback): https://console.groq.com → `GROQ_API_KEY=`

Restart the server. The sidebar shows *AI reader on*, and **How it works → Test the AI connection**
confirms the key works.

How the AI reader is kept reliable on a free tier:

- **One request per case.** All of a case's documents go to the model in a single call.
- **Failover chain.** `GEMINI_MODEL` lists several models, tried in order. A model that is
  rate-limited or slow is skipped for a minute.
- **Time budget.** If no model answers within about 14 seconds, the rule-based reader is used.
  A slow AI can't stall a run, and the case page says which reader was used.
- **Cache by file content.** The same file always gets the same reading, at no quota cost. The
  case page labels these `(cached)`. Run `python -m app.warm` before a demo to pre-read the
  scenario documents.

**See the AI do something rules can't:** on *New case → Drop documents*, drop the three files in
`data/samples/vindhya_tools/`. The cheque is a scanned image with no text layer, so only the vision
model can read it. (Dropping them a second time is flagged as a possible duplicate vendor, which is
correct. Use *Reset demo data* to start clean.)

## Layout

```
app/
  validators.py   GSTIN / PAN / IFSC / EIN / ABA checks, name matching     (pure functions, unit-tested)
  docreader.py    document classification + field extraction (rules, AI, guardrail)
  llm.py          Gemini → Groq → unavailable; standard library only
  pipeline.py     the eight stages, 20 checks, consistency matrix, decision
  messages.py     vendor message + internal note
  scenarios.py    the seven scenarios, vendor master, blocked list
  docgen.py       generates the test PDFs
  suite.py        runs every scenario and compares with the expected outcome
  db.py           SQLite: cases, versions, vendor master, audit log
  main.py         FastAPI: API, live event stream, document images with highlights
static/           the UI (no framework, no build step)
tests/            28 tests: validators and every scenario
```

## Deploy (Render, free)

1. Push this folder to a GitHub repository.
2. On https://render.com: *New → Blueprint* → pick the repository (it reads `render.yaml`).
3. Optionally add `GEMINI_API_KEY` / `GROQ_API_KEY` under *Environment*.

Free instances sleep when idle (the first request takes ~30 s) and have no persistent disk, so the
sample history is recreated on each start. That is fine for a demo.

## Assumptions and limits

- The buyer is an Indian mid-size company; vendors are mostly Indian, some from the US.
- No live calls to GSTN, MCA, the IRS or banks. Check digits and cross-document consistency stand
  in for registry lookups. Registry verification and a one-rupee test deposit are the first things
  to add.
- Emails are drafted, not sent.
- No login. The vendor fix link carries a per-case token.
- All companies, IDs and accounts are fictional. IDs are synthetic but pass their real checksums.
