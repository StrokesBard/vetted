"""Web server: API + live event stream + static UI.

Run:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import json
import shutil
import threading
from contextlib import asynccontextmanager
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, llm, suite
from .docreader import DOC_LABELS
from .pipeline import CHECKS, REQUIRED, SLOT_ASK, SLOTS, STAGES, Pipeline, merge_fix
from .scenarios import BLOCKLIST, SCENARIOS
from .validators import ENTITY_LABELS



@asynccontextmanager
async def lifespan(_app):
    suite.ensure_documents()
    db.init()
    if not db.list_cases():
        seed_history()
    yield


app = FastAPI(title="Vetted", docs_url="/api/docs", lifespan=lifespan)

# Live runs: "VND-0041:1" -> {"events": [...], "done": bool}. Finished runs are replayed from the database.
RUNS: dict[str, dict] = {}
ALLOWED_UPLOADS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


# ------------------------------------------------------------------ running

def _execute(case: dict, version: int, submission: dict, files: list[dict], channel: str, base_url: str,
             *, emit=None, delay=0.0, at: str | None = None, use_ai=True) -> dict:
    fix_link = f"{base_url}#/fix/{case['id']}?t={case['token']}"
    result = Pipeline(submission, files, channel=channel, vendors=db.vendors(seed_only=bool(case.get("scenario"))),
                      blocklist=BLOCKLIST,
                      emit=emit, delay=delay, use_ai=use_ai, fix_link=fix_link).run()
    stored = [{"slot": f["slot"], "path": str(f["path"]), "name": f["name"]} for f in files]
    db.save_version(case["id"], version, result, stored, at=at)
    db.log(case["id"], "system", f"Version {version} decided: {result['status']}", result["verdict"], at=at)
    if result["status"] == "approved" and not case.get("scenario"):
        db.add_vendor(case["id"], result["submission"])
    return result


def _start(case: dict, version: int, submission: dict, files: list[dict], channel: str, base_url: str) -> None:
    key = f"{case['id']}:{version}"
    run = RUNS[key] = {"events": [], "done": False}
    name = submission.get("company", {}).get("legal_name", "")
    db.set_running(case["id"], version, name, submission.get("company", {}).get("country", ""))

    def work():
        try:
            _execute(case, version, submission, files, channel, base_url,
                     emit=run["events"].append, delay=config.STAGE_DELAY)
        except Exception as e:   # never leave a run hanging in the UI
            run["events"].append({"type": "error", "message": f"{type(e).__name__}: {e}"})
            db.mark_error(case["id"], f"Run failed: {e}")
        finally:
            run["done"] = True

    threading.Thread(target=work, daemon=True).start()


def _store(case_id: str, version: int, slot_or_name: str, source: Path | None = None, data: bytes | None = None,
           suffix: str = ".pdf") -> Path:
    folder = config.UPLOAD_DIR / case_id / f"v{version}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{slot_or_name}{suffix}"
    if source:
        shutil.copyfile(source, target)
    else:
        target.write_bytes(data)
    return target


async def _read_upload(upload) -> tuple[bytes, str]:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOADS:
        raise HTTPException(400, f"{upload.filename}: only PDF, PNG, JPG or WEBP files are accepted.")
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"{upload.filename}: file is larger than 8 MB.")
    return data, suffix


def _scenario_case(key: str, base_url: str, *, sample=False, created_at=None, sync=False):
    sc = SCENARIOS[key]
    case = db.new_case(channel=sc["channel"], scenario=key, sample=sample, created_at=created_at)
    case["scenario"] = key
    files = []
    for n, d in enumerate(sc["docs"]):
        stored = _store(case["id"], 1, d["slot"] or f"file{n + 1}", source=config.SCENARIO_DIR / key / d["file"])
        files.append({"slot": d["slot"], "path": stored, "name": d["file"]})
    db.log(case["id"], "vendor", "Submission received", f"Demo scenario: {sc['title']}", at=created_at)
    if sync:
        _execute(case, 1, sc["submission"], files, sc["channel"], base_url, at=created_at, use_ai=False)
    else:
        _start(case, 1, sc["submission"], files, sc["channel"], base_url)
    return case


def _apply_fix(case_row: dict, fields: dict, new_files: dict[str, Path | tuple[bytes, str]], base_url: str,
               *, sync=False, at=None, names=None):
    """Create the next version of a case from the vendor's corrections."""
    version = case_row["version"] + 1
    prev = db.get_version(case_row["id"], case_row["version"])
    submission = merge_fix(prev["result"]["submission"], fields)
    files = []
    for f in prev["files"]:
        if f["slot"] and f["slot"] not in new_files:
            files.append({"slot": f["slot"], "name": f["name"],
                          "path": _store(case_row["id"], version, f["slot"], source=Path(f["path"]),
                                         suffix=Path(f["path"]).suffix)})
    for slot, item in new_files.items():
        name = (names or {}).get(slot, f"{slot}.pdf")
        if isinstance(item, Path):
            path = _store(case_row["id"], version, slot, source=item, suffix=item.suffix)
        else:
            path = _store(case_row["id"], version, slot, data=item[0], suffix=item[1])
        files.append({"slot": slot, "path": path, "name": name})
    changed = list(fields) + [f"document: {SLOTS[s]}" for s in new_files]
    db.log(case_row["id"], "vendor", f"Resubmitted (version {version})", "Changed: " + ", ".join(changed), at=at)
    case = {"id": case_row["id"], "token": case_row["token"], "scenario": case_row["scenario"]}
    if sync:
        _execute(case, version, submission, files, "portal", base_url, at=at, use_ai=False)
    else:
        _start(case, version, submission, files, "portal", base_url)
    return version


def seed_history(base_url: str = "/") -> None:
    """Fill an empty dashboard with real pipeline runs on the demo inputs, backdated over the last week."""
    start = datetime.now(timezone.utc)

    def ago(days, hours=0):
        return (start - timedelta(days=days, hours=hours)).isoformat(timespec="seconds")

    plan = [("us_vendor", 6, 3), ("name_variants", 5, 1), ("bank_reuse", 4, 5), ("wrong_document", 3, 2),
            ("clean", 2, 6), ("state_mismatch", 1, 4)]
    for key, days, hours in plan:
        case = _scenario_case(key, base_url, sample=True, created_at=ago(days, hours), sync=True)
        if key == "wrong_document":   # this vendor came back the next day with the right document
            row = db.get_case(case["id"])
            fix = SCENARIOS[key]["fix"]
            _apply_fix(row, fix["fields"], {d["slot"]: config.SCENARIO_DIR / key / d["file"] for d in fix["docs"]},
                       base_url, sync=True, at=ago(days - 1, hours), names={d["slot"]: d["file"] for d in fix["docs"]})


# ---------------------------------------------------------------------- API

@app.get("/api/meta")
def meta():
    return {
        "buyer": config.BUYER_NAME,
        "ai": llm.status(),
        "stages": [{"key": k, "label": l} for k, l in STAGES],
        "checks": [{"id": c, "stage": s, "title": t, "severity": sev} for c, s, t, sev in CHECKS],
        "slots": SLOTS, "slot_ask": SLOT_ASK, "doc_labels": DOC_LABELS, "entity_labels": ENTITY_LABELS,
        "required": {c: [{"path": p, "label": l} for p, l in rows] for c, rows in REQUIRED.items()},
        "scenarios": [{"key": k, "title": s["title"], "kind": s["kind"], "expect": s["expect"],
                       "expect_after_fix": s.get("expect_after_fix"), "blurb": s["blurb"], "has_fix": bool(s.get("fix")),
                       "docs": [d["file"] for d in s["docs"]]} for k, s in SCENARIOS.items()],
        "vendors": db.vendors(), "blocklist": BLOCKLIST,
        "manual_minutes": config.MANUAL_MINUTES_PER_CASE,
    }


@app.get("/api/ai/probe")
def ai_probe():
    return llm.probe() if llm.provider() else {"ok": False, "error": "No API key configured. Running on the rule-based reader."}


@app.get("/api/cases")
def cases():
    rows = db.list_cases()
    decided = [r for r in rows if r["status"] in ("approved", "pending", "rejected")]
    count = Counter(r["status"] for r in decided)
    reasons = Counter()
    titles = {c: t for c, _, t, _ in CHECKS}
    for result in db.latest_results():
        for check in {f["check"] for f in result["findings"] if f["severity"] in ("fix", "block")}:
            reasons[titles.get(check, check)] += 1
    n = len(decided)
    auto = sum(1 for r in decided if not r["overridden"])
    times = [r["work_ms"] for r in decided if r["work_ms"] is not None]
    return {
        "cases": rows,
        "metrics": {
            "total": n, "approved": count["approved"], "pending": count["pending"], "rejected": count["rejected"],
            "auto_rate": round(100 * auto / n) if n else 0,
            "first_pass_rate": round(100 * sum(1 for r in decided if r["status"] == "approved" and r["version"] == 1) / n) if n else 0,
            "avg_ms": round(sum(times) / len(times)) if times else 0,
            "minutes_saved": n * config.MANUAL_MINUTES_PER_CASE,
            "top_reasons": [{"reason": r, "count": c} for r, c in reasons.most_common(5)],
        },
    }


@app.post("/api/cases")
async def create_case(request: Request):
    form = await request.form()
    base_url = str(request.base_url)
    scenario = form.get("scenario")
    if scenario:
        if scenario not in SCENARIOS:
            raise HTTPException(404, "Unknown scenario.")
        case = _scenario_case(scenario, base_url)
        return {"case_id": case["id"], "version": 1}

    channel = form.get("channel", "portal")
    try:
        submission = json.loads(form.get("payload") or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "The form data couldn't be read.")
    uploads = []
    if channel == "documents_only":
        for n, upload in enumerate(form.getlist("files")):
            if getattr(upload, "filename", ""):
                uploads.append((None, f"file{n + 1}", upload))
        if not uploads:
            raise HTTPException(400, "Add at least one document.")
    else:
        for slot in SLOTS:
            upload = form.get(slot)
            if getattr(upload, "filename", ""):
                uploads.append((slot, slot, upload))
    read = [(slot, stem, upload.filename, *(await _read_upload(upload))) for slot, stem, upload in uploads]

    case = db.new_case(channel=channel, scenario=None, sample=False)
    case["scenario"] = None
    files = [{"slot": slot, "name": name, "path": _store(case["id"], 1, stem, data=data, suffix=suffix)}
             for slot, stem, name, data, suffix in read]
    db.log(case["id"], "vendor", "Submission received", f"{len(files)} documents via {channel}")
    _start(case, 1, submission, files, channel, base_url)
    return {"case_id": case["id"], "version": 1}


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str):
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found.")
    sc = SCENARIOS.get(case["scenario"] or "")
    case["demo_fix_available"] = bool(sc and sc.get("fix"))
    case["scenario_title"] = sc["title"] if sc else None
    return case


@app.get("/api/cases/{case_id}/v/{version}/events")
def events(case_id: str, version: int):
    key = f"{case_id}:{version}"

    def send(event):
        return f"data: {json.dumps(event)}\n\n"

    def stream():
        run = RUNS.get(key)
        if run is None:   # already finished (or the server restarted): replay from the stored record
            stored = db.get_version(case_id, version)
            if not stored:
                yield send({"type": "error", "message": "This run was not found."})
                return
            r = stored["result"]
            for stage in r["stages"]:
                yield send({"type": "stage_start", "stage": stage["key"], "label": stage["label"]})
                for f in r["findings"]:
                    if f["stage"] == stage["key"]:
                        yield send({"type": "finding", "finding": f})
                if stage["key"] == "cross_check" and r.get("matrix"):
                    yield send({"type": "matrix", "matrix": r["matrix"]})
                yield send({"type": "stage_done", **stage})
            yield send({"type": "decision", "status": r["status"], "verdict": r["verdict"]})
            yield send({"type": "saved", "replay": True, "work_ms": r["work_ms"]})
            return
        sent = 0
        while True:
            pending = run["events"][sent:]
            for event in pending:
                yield send(event)
            sent += len(pending)
            if run["done"] and sent == len(run["events"]):
                stored = db.get_version(case_id, version)
                yield send({"type": "saved", "work_ms": stored["result"]["work_ms"] if stored else None})
                return
            time.sleep(0.06)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/fix/{case_id}")
def fix_page(case_id: str, t: str = ""):
    """What the vendor's fix page is allowed to see: only what they are being asked for."""
    case = db.get_case(case_id)
    if not case or t != case["token"]:
        raise HTTPException(403, "This link isn't valid. Please use the link from our email.")
    latest = case["versions"][-1]["result"] if case["versions"] else None
    sc = SCENARIOS.get(case["scenario"] or "")
    return {
        "buyer": config.BUYER_NAME, "status": case["status"], "vendor_name": case["vendor_name"],
        "contact_name": latest["submission"].get("company", {}).get("contact_name", "") if latest else "",
        "asks": latest["asks"] if latest and case["status"] == "pending" else [],
        "demo_fix_available": bool(sc and sc.get("fix")),
    }


@app.post("/api/cases/{case_id}/resubmit")
async def resubmit(case_id: str, request: Request):
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found.")
    form = await request.form()
    if form.get("t") != case["token"]:
        raise HTTPException(403, "This fix link isn't valid.")
    if case["status"] != "pending":
        raise HTTPException(409, "This case isn't waiting for anything.")
    names = {}
    if form.get("demo_fix"):
        sc = SCENARIOS.get(case["scenario"] or "")
        if not (sc and sc.get("fix")):
            raise HTTPException(400, "No demo fix exists for this case.")
        fields = sc["fix"]["fields"]
        new_files = {d["slot"]: config.SCENARIO_DIR / case["scenario"] / d["file"] for d in sc["fix"]["docs"]}
        names = {d["slot"]: d["file"] for d in sc["fix"]["docs"]}
    else:
        try:
            fields = json.loads(form.get("fields") or "{}")
        except json.JSONDecodeError:
            raise HTTPException(400, "The corrections couldn't be read.")
        new_files = {}
        for slot in SLOTS:
            upload = form.get(slot)
            if getattr(upload, "filename", ""):
                new_files[slot] = await _read_upload(upload)
                names[slot] = upload.filename
        if not fields and not new_files:
            raise HTTPException(400, "Nothing was changed.")
    version = _apply_fix(case, fields, new_files, str(request.base_url), names=names)
    return {"case_id": case_id, "version": version}


@app.post("/api/cases/{case_id}/override")
async def override(case_id: str, request: Request):
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found.")
    body = await request.json()
    status, reason = body.get("status"), (body.get("reason") or "").strip()
    reviewer = (body.get("reviewer") or "Reviewer").strip()
    if status not in ("approved", "pending", "rejected"):
        raise HTTPException(400, "Choose approved, pending or rejected.")
    if len(reason) < 10:
        raise HTTPException(400, "Give a reason of at least 10 characters. It goes in the audit log.")
    db.set_override(case_id, status, reason, reviewer)
    db.log(case_id, reviewer, f"Overrode the decision: {case['status']} to {status}", reason)
    if status == "approved" and not case["scenario"] and case["versions"]:
        db.add_vendor(case_id, case["versions"][-1]["result"]["submission"])
    return {"ok": True}


def _file_for(case_id: str, version: int, slot: str) -> Path:
    stored = db.get_version(case_id, version)
    if not stored:
        raise HTTPException(404, "Version not found.")
    for f in stored["files"]:
        path = Path(f["path"])
        if f["slot"] == slot or path.stem == slot:
            resolved = path.resolve()
            if config.UPLOAD_DIR.resolve() not in resolved.parents or not resolved.exists():
                break
            return resolved
    raise HTTPException(404, "Document not found.")


@app.get("/api/cases/{case_id}/v/{version}/doc/{slot}.png")
def doc_image(case_id: str, version: int, slot: str, hl: str = ""):
    """First page of a document as an image, with the given text highlighted (evidence view)."""
    path = _file_for(case_id, version, slot)
    with pymupdf.open(str(path)) as doc:
        page = doc[0]
        hits = 0
        for term in [t for t in hl.split("|") if len(t.strip()) >= 3][:4]:
            rects = page.search_for(term.strip())
            if rects:
                page.add_highlight_annot(rects)
                hits += len(rects)
        zoom = min(2.0, 1100 / page.rect.width)
        png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
    return Response(png, media_type="image/png", headers={"X-Highlights": str(hits), "Cache-Control": "no-store"})


@app.get("/api/cases/{case_id}/v/{version}/doc/{slot}/file")
def doc_file(case_id: str, version: int, slot: str):
    return FileResponse(_file_for(case_id, version, slot))


@app.post("/api/suite/run")
def suite_run():
    started = time.perf_counter()
    rows = suite.run_all()
    return {"rows": rows, "passed": sum(r["ok"] for r in rows), "total": len(rows),
            "ms": int((time.perf_counter() - started) * 1000)}


@app.post("/api/reset")
def reset(request: Request):
    RUNS.clear()
    db.reset()
    seed_history(str(request.base_url))
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# ------------------------------------------------------------------- static

@app.get("/")
def index():
    # Stamp the script and stylesheet URLs with their last-modified time, so a browser never
    # keeps running an old copy after the files change.
    html = (config.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css"):
        stamp = int((config.STATIC_DIR / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
