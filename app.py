"""Flask validation app: turn GLM-OCR markdown into reviewed, verified data.

Single-user local tool (localhost:5000). Item-first review: tables and
equations are the primary targets, page text is secondary.
"""
import json
import os
import re
import threading
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file  # pyright: ignore[reportMissingImports] — env lacks flask; runtime python3 has it

import config
from itemizer import parse_document  # pyright: ignore[reportMissingImports] — same-dir module
from ocr_engine import assemble_markdown, ocr_batch, ocr_page, pdf_to_images

BASE = Path(__file__).resolve().parent
VALIDATION = BASE / "validation"
PENDING_DIR = VALIDATION / "pending"
VERIFIED_DIR = VALIDATION / "verified"
REJECTED_DIR = VALIDATION / "rejected"
UPLOADS = Path(config.UPLOAD_DIR)
if not UPLOADS.is_absolute():
    UPLOADS = BASE / config.UPLOAD_DIR

for d in (PENDING_DIR, VERIFIED_DIR, REJECTED_DIR, UPLOADS):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

JOBS = {}  # ponytail: in-memory only — jobs lost on restart; persistent queue if that matters


def doc_id_from_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem) or "doc"


def pending_path(doc_id):
    return PENDING_DIR / f"{doc_id}.json"


def load_doc(doc_id):
    p = pending_path(doc_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None  # corrupt/half-written file: treat as missing


def save_doc(doc):
    (PENDING_DIR / f"{doc['doc_id']}.json").write_text(json.dumps(doc, indent=2))


def page_count(pdf_path):
    import pymupdf

    with pymupdf.open(pdf_path) as pdf:
        return len(pdf)


def parse_and_persist(doc):
    """Re-parse the doc's markdown into page-scoped items, persist."""
    md = Path(doc["md_path"])
    doc["pages"] = parse_document(md.read_text(), doc["doc_id"])
    if not doc.get("n_pages"):
        doc["n_pages"] = page_count(Path(doc["pdf_path"]))
    save_doc(doc)
    return doc


def append_bbox_items(doc, page, markdown):
    """Parse a crop's OCR text and append its items to the target page.

    Returns the number of items added.
    """
    new_items = [
        it for pg in parse_document(markdown, doc["doc_id"]) for it in pg["items"]
    ]
    if not new_items:
        return 0
    target = next((p for p in doc.get("pages", []) if p["page"] == page), None)
    if target is None:
        target = {"page": page, "items": []}
        doc.setdefault("pages", []).append(target)
        doc["pages"].sort(key=lambda p: p["page"])
    for it in new_items:
        it["id"] = f"{doc['doc_id']}-p{page}-bbox{uuid.uuid4().hex[:8]}"
        it["status"] = "pending"
        target["items"].append(it)
    return len(new_items)


def apply_action(doc, item_id, action, content=None):
    """Update an item's status in doc; write verified/rejected JSON copies."""
    for page in doc.get("pages", []):
        for item in page["items"]:
            if item["id"] != item_id:
                continue
            if item["status"] in ("verified", "rejected"):
                return True  # already finalized: further actions are no-ops
            item["status"] = action if action in ("verified", "rejected", "skipped") else item["status"]
            if action in ("accept", "reject"):
                final = content if content not in (None, "") else item["content"]
                payload = {
                    "doc_id": doc["doc_id"],
                    "item_id": item_id,
                    "page": page["page"],
                    "type": item["type"],
                    "content": final,
                }
                target = VERIFIED_DIR if action == "accept" else REJECTED_DIR
                (target / f"{item_id}.json").write_text(json.dumps(payload, indent=2))
                item["status"] = "verified" if action == "accept" else "rejected"
                if action == "accept" and content not in (None, ""):
                    item["content"] = content  # keep edited content as source of truth
            else:  # skip
                item["status"] = "skipped"
            return True
    return False


def docs_list():
    out = []
    for p in sorted(PENDING_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue  # corrupt file: skip listing
        counts = {"pending": 0, "skipped": 0, "verified": 0, "rejected": 0}
        for page in d.get("pages", []):
            for it in page["items"]:
                counts[it["status"]] = counts.get(it["status"], 0) + 1
        out.append({
            "doc_id": d["doc_id"],
            "source_name": d.get("source_name", ""),
            "n_pages": d.get("n_pages", 0),
            "counts": counts,
        })
    return out


@app.route("/")
def index():
    return render_template("index.html", docs=docs_list(), doc=None)


@app.route("/load", methods=["POST"])
def load():
    pdf = request.form.get("pdf_path", "").strip()
    md = request.form.get("md_path", "").strip()
    if not pdf or not md:
        abort(400, "pdf_path and md_path are required")
    pdf_p, md_p = Path(pdf), Path(md)
    if not pdf_p.is_file() or not md_p.is_file():
        abort(400, "paths must point to existing files")
    doc_id = doc_id_from_name(md_p)
    doc = {
        "doc_id": doc_id,
        "source_name": pdf_p.name,
        "pdf_path": str(pdf_p),
        "md_path": str(md_p),
    }
    parse_and_persist(doc)
    return redirect(f"/doc/{doc_id}")


@app.route("/upload", methods=["POST"])
def upload():
    pdf_file = request.files.get("pdf")
    if not pdf_file or not pdf_file.filename:
        abort(400, "pdf file required")
    doc_id = doc_id_from_name(pdf_file.filename)
    doc_dir = UPLOADS / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = doc_dir / f"{doc_id}.pdf"
    pdf_file.save(str(pdf_path))
    md_path = doc_dir / f"{doc_id}.md"

    doc = {
        "doc_id": doc_id,
        "source_name": pdf_file.filename,
        "pdf_path": str(pdf_path),
        "md_path": str(md_path),
        "n_pages": page_count(pdf_path),
    }
    md_file = request.files.get("md")
    has_md = bool(md_file and md_file.filename and md_file.filename.endswith(".md"))
    if md_file is not None and has_md:
        md_file.save(str(md_path))
        parse_and_persist(doc)
    else:
        doc["pages"] = []
        save_doc(doc)
    # Native form submit -> redirect. AJAX fetch -> JSON.
    accept = request.headers.get("Accept", "")
    if "text/html" == request.accept_mimetypes.best:
        return redirect(f"/doc/{doc_id}?ocr=1" if not has_md else f"/doc/{doc_id}")
    return jsonify({"doc_id": doc_id, "ocr_needed": not has_md})


@app.route("/ocr/<doc_id>", methods=["POST"])
def start_ocr(doc_id):
    doc = load_doc(doc_id)
    if doc is None:
        abort(404)
        return
    if doc.get("pages"):
        abort(400, "document already has parsed markdown")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running"}
    threading.Thread(target=_run_ocr, args=(job_id, doc), daemon=True).start()
    return jsonify({"job_id": job_id})


def _run_ocr(job_id, doc):
    try:
        if not config.API_KEY:
            raise RuntimeError("UNSLOTH_API_KEY not set: set it to run GLM-OCR")
        pdf = Path(doc["pdf_path"])
        images, tmpdir = pdf_to_images(pdf)
        try:
            results = ocr_batch(images, workers=2)
        finally:
            for _, img in images:
                try:
                    os.remove(img)
                except OSError:
                    pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
        markdown = assemble_markdown(results, source_name=doc["source_name"])
        Path(doc["md_path"]).write_text(markdown)
        parse_and_persist(load_doc(doc["doc_id"]))  # fresh dict -> pages
        JOBS[job_id]["status"] = "done"
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}


@app.route("/doc/<doc_id>/discard", methods=["POST"])
def discard_doc(doc_id):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", doc_id) or load_doc(doc_id) is None:
        abort(404)
    pending_path(doc_id).unlink(missing_ok=True)
    up = UPLOADS / doc_id
    if up.is_dir():
        import shutil

        try:
            shutil.rmtree(up)
        except OSError:
            pass  # ponytail: leftover uploads dir is harmless; retry by hand if it matters
    for d in (VERIFIED_DIR, REJECTED_DIR):
        for f in d.glob(f"{doc_id}-*.json"):
            f.unlink(missing_ok=True)
    return jsonify({"ok": True})


@app.route("/bbox_ocr/<doc_id>", methods=["POST"])
def bbox_ocr(doc_id):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", doc_id):
        abort(404)
    doc = load_doc(doc_id)
    if doc is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    try:
        page = int(data.get("page", 1))
        fx, fy, fw, fh = (float(data[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        abort(400, "page, x, y, w, h required (fractions of the page, 0..1)")
    if fw <= 0 or fh <= 0:
        abort(400, "box width/height must be positive")

    import pymupdf

    with pymupdf.open(doc["pdf_path"]) as pdf:
        if page < 1 or page > len(pdf):
            abort(404, "page out of range")
        pr = pdf[page - 1].rect
        clip = pymupdf.Rect(
            pr.x0 + fx * pr.width, pr.y0 + fy * pr.height,
            pr.x0 + (fx + fw) * pr.width, pr.y0 + (fy + fh) * pr.height,
        )
        pix = pdf[page - 1].get_pixmap(
            matrix=pymupdf.Matrix(200 / 72, 200 / 72), clip=clip
        )

    tmp = UPLOADS / doc_id / f"bbox_{page}_{uuid.uuid4().hex[:8]}.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(tmp))
    try:
        result = ocr_page(str(tmp), page_num=page)
    finally:
        tmp.unlink(missing_ok=True)

    added = append_bbox_items(doc, page, result["text"])
    save_doc(doc)
    return jsonify({"ok": True, "doc": doc, "added": added})


@app.route("/jobs/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        abort(404)
    return jsonify(job)


@app.route("/doc/<doc_id>")
def doc_view(doc_id):
    doc = load_doc(doc_id)
    if doc is None:
        abort(404)
    return render_template("index.html", docs=docs_list(), doc=doc)


@app.route("/page/<doc_id>/<int:n>.png")
def page_png(doc_id, n):
    doc = load_doc(doc_id)
    if doc is None:
        abort(404)
        return
    cache = UPLOADS / doc_id / f"page_{n:04d}.png"
    if not cache.exists():
        import pymupdf

        with pymupdf.open(doc["pdf_path"]) as pdf:
            if n < 1 or n > len(pdf):
                abort(404)
            pix = pdf[n - 1].get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72))
        cache.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(cache))
    return send_file(cache, mimetype="image/png")


@app.route("/item/<doc_id>/<item_id>/action", methods=["POST"])
def item_action(doc_id, item_id):
    doc = load_doc(doc_id)
    if doc is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("accept", "reject", "skip"):
        abort(400, "action must be accept|reject|skip")
    if not apply_action(doc, item_id, action, data.get("content")):
        abort(404, f"item {item_id} not found")
    save_doc(doc)
    return jsonify({"ok": True, "doc": doc})


@app.route("/bulk", methods=["POST"])
def bulk():
    data = request.get_json(silent=True) or {}
    doc = load_doc(data.get("doc_id", ""))
    if doc is None:
        abort(404)
        return
    action = data.get("action", "skip")
    if action not in ("skip", "accept"):
        abort(400, "bulk action must be skip|accept")
    targets = set(data.get("item_ids", []))
    updated = 0
    for page in doc.get("pages", []):
        for item in page["items"]:
            if item["id"] not in targets or item["status"] in ("verified", "rejected"):
                continue  # finalized items are never bulk-mutated
            if item["status"] == ("skipped" if action == "skip" else "verified"):
                continue  # already in the target state: nothing changes
            apply_action(doc, item["id"], action)
            updated += 1
    save_doc(doc)
    return jsonify({"ok": True, "updated": updated, "doc": doc})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")