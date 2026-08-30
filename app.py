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
from itemizer import clean_export_text, parse_document, parse_table_caption, pick_caption_from_band, unwrap_html_caption  # pyright: ignore[reportMissingImports] — same-dir module
from ocr_engine import CAPTION_BAND_PROMPT, assemble_markdown, ocr_batch, ocr_page, parse_page_range, pdf_to_images, tesseract_ocr

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
                    "chapter": item.get("chapter"),
                    "section": item.get("section"),
                    "source_name": doc.get("source_name", ""),
                    "content": clean_export_text(final),
                }
                if item.get("caption") is not None:
                    payload["caption"] = item["caption"]
                    payload["table_number"] = item.get("table_number")
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
    pages_raw = request.form.get("ocr_pages", "").strip()
    page_range = None
    if pages_raw:
        page_range = parse_page_range(pages_raw)
        if page_range is None:
            abort(400, "invalid ocr_pages (use e.g. 5-15 or 5)")
    if md_file is not None and has_md:
        md_file.save(str(md_path))
        parse_and_persist(doc)
    else:
        doc["pages"] = []
        if page_range:
            doc["ocr_pages"] = list(page_range)
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
    JOBS[job_id] = {"status": "running", "done": 0, "total": 0}
    threading.Thread(target=_run_ocr, args=(job_id, doc), daemon=True).start()
    return jsonify({"job_id": job_id})


def _run_ocr(job_id, doc):
    try:
        if not config.API_KEY:
            raise RuntimeError("UNSLOTH_API_KEY not set: set it to run GLM-OCR")
        pdf = Path(doc["pdf_path"])
        ocr_pages = doc.get("ocr_pages")
        page_range = tuple(ocr_pages) if isinstance(ocr_pages, (list, tuple)) and len(ocr_pages) == 2 else None
        images, tmpdir = pdf_to_images(pdf, page_range=page_range)
        if not images:
            raise RuntimeError(
                f"no pages match the OCR page range (PDF has {page_count(pdf)} pages)"
            )
        JOBS[job_id]["total"] = len(images)
        try:
            results = ocr_batch(
                images, workers=2,
                on_progress=lambda done, total: JOBS[job_id].update(
                    done=done, total=total
                ),
            )
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


def _render_crop(doc, page, box):
    """Render a page region (fractions 0..1) to a temp PNG; returns the Path.

    Raises ValueError for out-of-range pages or non-positive w/h. The caller
    owns the temp file and must delete it.
    """
    try:
        fx, fy, fw, fh = (float(box[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("x, y, w, h required (fractions of the page, 0..1)") from e
    if fw <= 0 or fh <= 0:
        raise ValueError("box width/height must be positive")

    import pymupdf

    with pymupdf.open(doc["pdf_path"]) as pdf:
        if page < 1 or page > len(pdf):
            raise ValueError("page out of range")
        pr = pdf[page - 1].rect
        clip = pymupdf.Rect(
            pr.x0 + fx * pr.width, pr.y0 + fy * pr.height,
            pr.x0 + (fx + fw) * pr.width, pr.y0 + (fy + fh) * pr.height,
        )
        pix = pdf[page - 1].get_pixmap(
            matrix=pymupdf.Matrix(200 / 72, 200 / 72), clip=clip
        )

    tmp = UPLOADS / doc["doc_id"] / f"crop_{page}_{uuid.uuid4().hex[:8]}.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(tmp))
    return tmp


def _ocr_page_crop(doc, page, box):
    """Render a page region and OCR it with GLM-OCR; returns the text."""
    tmp = _render_crop(doc, page, box)
    try:
        return ocr_page(str(tmp), page_num=page)["text"]
    finally:
        tmp.unlink(missing_ok=True)


def _ocr_band(doc, page, box, dy, height=0.02):
    """OCR a tight horizontal slice above/at the top of box, caption-focused.

    Captions sit one line above their table; GLM drops the caption under the
    generic prompt, so this uses CAPTION_BAND_PROMPT.
    """
    band = dict(box)
    band["y"] = max(0.0, box["y"] - dy)
    band["h"] = height
    tmp = _render_crop(doc, page, band)
    try:
        return ocr_page(str(tmp), page_num=page, prompt=CAPTION_BAND_PROMPT)["text"]
    finally:
        tmp.unlink(missing_ok=True)


def _attach_caption_to_first_table(doc, page, box, items_before):
    """Best-effort: caption the first table added by a bbox draw.

    Tries two tight bands above/at the box top (the caption may sit one line
    above the table, depending on where the user started the box). Only sets a
    caption if band OCR starts with a real "Table N…" line (strict parse);
    never overwrites; any OCR hiccup leaves the table uncaptioned.
    """
    if box.get("h", 0) < 0.02:  # tiny box: header/row re-OCR, no caption
        return False
    for dy in (0.016, 0.004):
        try:
            caption, num = pick_caption_from_band(_ocr_band(doc, page, box, dy))
        except Exception:  # ponytail: band OCR is best-effort
            continue
        if caption is None:
            continue
        target = next(
            (pg for pg in doc.get("pages", []) if pg["page"] == page), None
        )
        if target is None:
            return False
        for item in target["items"]:
            if item["id"] in items_before:
                continue
            if item.get("type") == "table" and item.get("caption") is None:
                item["caption"] = caption
                item["table_number"] = num
                return True
    return False


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
        box = {k: float(data[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        abort(400, "page, x, y, w, h required (fractions of the page, 0..1)")

    try:
        text = _ocr_page_crop(doc, page, box)
    except ValueError as e:
        abort(404, str(e))
    except Exception as e:  # OCR/backend failure -> JSON error, not a crash
        return jsonify({"ok": False, "error": str(e)}), 502

    items_before = set(
        it["id"] for pg in doc.get("pages", []) for it in pg["items"]
    )
    added = append_bbox_items(doc, page, text)
    _attach_caption_to_first_table(doc, page, box, items_before)
    save_doc(doc)
    return jsonify({"ok": True, "doc": doc, "added": added})


@app.route("/item/<doc_id>/<item_id>/caption", methods=["POST"])
def item_caption(doc_id, item_id):
    """OCR a drawn region and use it as the caption of a table item.

    engine="glm" (default) runs ocr_page, "tesseract" the local CLI. The
    result is parsed by parse_table_caption and replaces/adds the item's
    caption and table_number.
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+", doc_id):
        abort(404)
    doc = load_doc(doc_id)
    if doc is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    try:
        page = int(data.get("page", 1))
        box = {k: float(data[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        abort(400, "page, x, y, w, h required (fractions of the page, 0..1)")
    engine = data.get("engine", "glm")
    if engine not in ("glm", "tesseract"):
        abort(400, "engine must be glm|tesseract")

    item = next(
        (it for pg in doc.get("pages", []) for it in pg["items"] if it["id"] == item_id),
        None,
    )
    if item is None:
        abort(404, f"item {item_id} not found")
    if item.get("type") != "table":
        abort(400, "caption applies to table items only")

    try:
        tmp = _render_crop(doc, page, box)
        try:
            if engine == "tesseract":
                result = tesseract_ocr(str(tmp))
            else:
                result = ocr_page(str(tmp), page_num=page)
        finally:
            tmp.unlink(missing_ok=True)
    except ValueError as e:
        abort(404, str(e))
    except Exception as e:  # OCR/backend failure -> JSON error, not a 500
        return jsonify({"ok": False, "error": str(e)}), 502

    caption, table_number = parse_table_caption(unwrap_html_caption(result["text"]))
    if caption is None:
        return jsonify({"ok": False, "error": "no caption text in the selected region"}), 400
    item["caption"] = caption
    item["table_number"] = table_number
    save_doc(doc)
    return jsonify({"ok": True, "doc": doc, "caption": caption, "table_number": table_number})


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