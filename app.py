"""Flask validation app: turn GLM-OCR markdown into reviewed, verified data.

Single-user local tool (localhost:5000). Item-first review: tables and
equations are the primary targets, page text is secondary.
"""
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file  # pyright: ignore[reportMissingImports] — env lacks flask; runtime python3 has it

import config
import models  # pyright: ignore[reportMissingImports] — same-dir module
import orchestrator  # pyright: ignore[reportMissingImports] — same-dir module
import rag_uploader as rag  # pyright: ignore[reportMissingImports] — same-dir module
from itemizer import INLINE_MATH_RE, clean_export_text, eq_refs, parse_document, parse_table_caption, pick_caption_from_band, unwrap_html_caption  # pyright: ignore[reportMissingImports] — same-dir module
from ocr_engine import CAPTION_BAND_PROMPT, EQUATION_PROMPT, OCR_PROMPT, assemble_markdown, ocr_batch, ocr_page, parse_page_ranges, pdf_to_images, tesseract_ocr

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

SESSIONS_DIR = BASE / "sessions"  # gitignored; one JSON file per chat session
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
MAX_CHAT_TOKENS = 12000  # ponytail: fixed cap, revisit if a legit answer truncates

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


def _parse_box(data):
    """Parse {x, y, w, h} (fractions 0..1) from a JSON request body.

    Raises ValueError for missing/non-numeric keys and non-finite values
    (NaN/Inf — float() happily accepts them and they bypass range guards).
    """
    try:
        box = {k: float(data[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("x, y, w, h required (fractions of the page, 0..1)") from e
    if not all(math.isfinite(v) for v in box.values()):
        raise ValueError("x, y, w, h must be finite numbers")
    return box


def parse_and_persist(doc):
    """Re-parse the doc's markdown into page-scoped items, persist."""
    md = Path(doc["md_path"])
    doc["pages"] = parse_document(md.read_text(), doc["doc_id"])
    if not doc.get("n_pages"):
        doc["n_pages"] = page_count(Path(doc["pdf_path"]))
    save_doc(doc)
    return doc


PAGE_SEP = re.compile(r"^--- Page (\d+) ---\s*$")


def merge_ocr_markdown(existing_md, new_md):
    """Merge freshly OCR'd pages into existing markdown (incremental OCR).

    existing_md: current document markdown (kept intact for unlisted pages).
    new_md:      assemble_markdown output for the new pages only (no header).
    Returns the full document: the leading `# OCR: …` header preserved, page
    blocks keyed by page number merged (new blocks replace same-numbered old
    ones), reassembled in ascending page order. Callers pass only pages that
    are genuinely new (the route subtracts covered pages) so in practice
    nothing existing gets replaced.
    """
    def split(md):
        header = ""
        blocks = {}
        page, buf = None, []
        for line in md.splitlines():
            m = PAGE_SEP.match(line)
            if m:
                try:
                    n = int(m.group(1))
                except ValueError:  # unreachable (regex digits); parity with itemizer
                    buf.append(line)
                    continue
                if page is not None:
                    blocks[page] = "\n".join(buf).strip()
                elif buf:
                    header = "\n".join(buf).strip()
                page, buf = n, []
            else:
                buf.append(line)
        if page is not None:
            blocks[page] = "\n".join(buf).strip()
        elif buf:
            header = header or "\n".join(buf).strip()
        return header, blocks

    header, blocks = split(existing_md)
    _, new_blocks = split(new_md)
    blocks.update(new_blocks)
    parts = [header] if header else []
    for pg in sorted(blocks):
        parts.append(f"\n--- Page {pg} ---\n{blocks[pg]}")
    return "\n".join(parts)


def merge_pages_into_doc(doc, new_pages, results):
    """Merge OCR results for new pages into the doc and persist.

    Rewrites the doc's markdown (existing pages verbatim, new page blocks
    added), then re-parses and restores review state (status, edited content,
    table_spans, eq keys) for every item that already existed — item ids are
    deterministic (``<doc_id>-p<n>-i<k>``), so unchanged pages parse to the
    same ids and the old dicts are copied back onto the fresh parse. New
    pages' items stay pending. Never re-OCR'd or replaced: verified/rejected
    state survives an incremental merge.
    """
    md_path = Path(doc["md_path"])
    existing = md_path.read_text() if md_path.exists() else ""
    new_md = assemble_markdown(results, source_name="")
    markdown = merge_ocr_markdown(existing, new_md) if existing.strip() else \
        assemble_markdown(results, source_name=doc["source_name"])
    md_path.write_text(markdown)
    old_pages = {p["page"]: p for p in (doc.get("pages") or [])}
    old_items = {
        it["id"]: it for p in old_pages.values() for it in p.get("items", [])
    }
    doc["pages"] = parse_document(markdown, doc["doc_id"])
    for pg in doc["pages"]:
        fresh_ids = {it["id"] for it in pg["items"]}
        for it in pg["items"]:
            old = old_items.get(it["id"])
            if old is not None:
                it.update(old)  # restore status, edits, spans, eq keys
        # Re-attach page-dict-only items (drawn-box crops) that the fresh
        # parse cannot reproduce: they live in the page dict, not the md.
        old_pg = old_pages.get(pg["page"])
        if old_pg:
            kept = [it for it in old_pg.get("items", []) if it["id"] not in fresh_ids]
            pg["items"] = pg["items"] + kept
    if not doc.get("n_pages"):
        doc["n_pages"] = page_count(Path(doc["pdf_path"]))
    save_doc(doc)
    return doc


def _target_page(doc, page):
    """Get (creating if needed) the page dict for a page number."""
    target = next((p for p in doc.get("pages", []) if p["page"] == page), None)
    if target is None:
        target = {"page": page, "items": []}
        doc.setdefault("pages", []).append(target)
        doc["pages"].sort(key=lambda p: p["page"])
    return target


def append_bbox_items(doc, page, markdown):
    """Parse a crop's OCR text and append its items to the target page.

    Returns the number of items added.
    """
    new_items = [
        it for pg in parse_document(markdown, doc["doc_id"]) for it in pg["items"]
    ]
    if not new_items:
        return 0
    target = _target_page(doc, page)
    for it in new_items:
        it["id"] = f"{doc['doc_id']}-p{page}-bbox{uuid.uuid4().hex[:8]}"
        it["status"] = "pending"
        target["items"].append(it)
    return len(new_items)


# GLM sometimes wraps drawn crops in HTML <table> markup under any prompt;
# strip just the wrapper tags (math spans can legitimately contain other chars).
# Table kinds keep the raw text: the wrapper may be the only structure for a
# forced table draw (auto mode converts HTML tables properly).
HTML_TABLE_TAG_RE = re.compile(
    r"</?(?:table|thead|tbody|tfoot|tr|th|td)\b[^>]*>", re.IGNORECASE
)


def append_bbox_item(doc, page, text, kind):
    """Append ONE item of a forced kind from a crop's OCR text (no parsing).

    kind is 'equation' | 'table' | 'text' (caller validates). Inherits
    chapter/section from the page's latest item that carries them. Equation
    kinds capture eq_letters/eq_num via eq_refs; text kinds flag inline math.
    Returns the new item.
    """
    target = _target_page(doc, page)
    content = text.strip()
    if kind in ("equation", "text"):
        # drop the HTML table wrapper GLM sometimes emits; keep the content
        cleaned = HTML_TABLE_TAG_RE.sub(" ", content)
        if cleaned != content:
            content = re.sub(r"\s+", " ", cleaned).strip()
    item = {
        "id": f"{doc['doc_id']}-p{page}-bbox{uuid.uuid4().hex[:8]}",
        "status": "pending",
        "type": kind,
        "content": content,
        "chapter": None,
        "section": None,
    }
    if kind == "text":
        item["has_inline_math"] = bool(INLINE_MATH_RE.search(content))
    if kind == "equation":
        letters, num = eq_refs(content, content)
        if letters is not None:
            item["eq_letters"] = letters
        if num is not None:
            item["eq_num"] = num
    for prev in reversed(target["items"]):  # inherit from latest item that has it
        if prev.get("chapter") is not None:
            item["chapter"] = prev["chapter"]
        if prev.get("section") is not None:
            item["section"] = prev["section"]
        if item["chapter"] is not None and item["section"] is not None:
            break
    target["items"].append(item)
    return item


def apply_action(doc, item_id, action, content=None, table_spans=None, eq_num=None, eq_letters=None):
    """Update an item's status in doc; write verified/rejected JSON copies.

    eq_num/eq_letters (equation items): None = leave as-is; "" = clear the
    key; any other string sets it. The client owns the equation key — it is
    never re-derived from the text here.
    """
    for page in doc.get("pages", []):
        for item in page["items"]:
            if item["id"] != item_id:
                continue
            if item["status"] in ("verified", "rejected"):
                # finalized; only an explicit flip to the OTHER state is allowed
                if action == "skip" or item["status"] == ("verified" if action == "accept" else "rejected"):
                    if action != "accept" or content in (None, ""):
                        return True  # same state or skip: no-op (accept w/ content still updates)
            item["status"] = action if action in ("verified", "rejected", "skipped") else item["status"]
            if action in ("accept", "reject"):
                if table_spans is not None:
                    item["table_spans"] = table_spans
                final = content if content not in (None, "") else item["content"]
                if item["type"] == "equation":
                    # explicit key edits only: None = untouched, "" = cleared
                    if eq_num is not None:
                        if eq_num == "":
                            item.pop("eq_num", None)
                        else:
                            item["eq_num"] = eq_num
                    if eq_letters is not None:
                        if eq_letters == "":
                            item.pop("eq_letters", None)
                        else:
                            item["eq_letters"] = eq_letters
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
                if item.get("table_spans") is not None:
                    payload["table_spans"] = item["table_spans"]
                if item["type"] == "equation" and item.get("eq_num") is not None:
                    payload["eq_num"] = item["eq_num"]
                    if item.get("eq_letters") is not None:
                        payload["eq_letters"] = item["eq_letters"]
                target = VERIFIED_DIR if action == "accept" else REJECTED_DIR
                doc_dir = target / doc["doc_id"]
                doc_dir.mkdir(parents=True, exist_ok=True)
                (doc_dir / f"{item_id}.json").write_text(json.dumps(payload, indent=2))
                stale = REJECTED_DIR if action == "accept" else VERIFIED_DIR
                (stale / doc["doc_id"] / f"{item_id}.json").unlink(missing_ok=True)  # drop old copy on flip
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


# --- chat sessions (one JSON file per session, gitignored) ---


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session_path(sid):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        return None
    return SESSIONS_DIR / f"{sid}.json"


def load_session(sid):
    p = session_path(sid)
    if not p or not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None  # corrupt: treat as missing


def save_session(s):
    (SESSIONS_DIR / f"{s['id']}.json").write_text(json.dumps(s, indent=2))


def sessions_list():
    out = []
    for p in SESSIONS_DIR.glob("*.json"):
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return out


@app.route("/")
def index():
    return render_template("index.html", docs=docs_list(), doc=None, tab="ocr", page_model="ocr")


@app.route("/chat")
def chat_page():
    sid = request.args.get("s", "")
    session = load_session(sid) if sid else None
    return render_template("chat.html", session=session, tab="chat", page_model="chat")


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
    page_ranges = None
    if pages_raw:
        page_ranges = parse_page_ranges(pages_raw)
        if page_ranges is None:
            abort(400, "invalid ocr_pages (use e.g. 5-15 or 2-3, 4-9)")
    if md_file is not None and has_md:
        md_file.save(str(md_path))
        parse_and_persist(doc)
    else:
        doc["pages"] = []
        if page_ranges:
            doc["ocr_pages"] = [list(r) for r in page_ranges]
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
    has_items = any(p.get("items") for p in (doc.get("pages") or []))
    if not has_items:
        # Fresh doc: full-file OCR (page_range honors stored ocr_pages).
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"status": "running", "done": 0, "total": 0}
        threading.Thread(target=_run_ocr, args=(job_id, doc), daemon=True).start()
        return jsonify({"job_id": job_id})

    # Already has items: incremental OCR — a range is required, and only
    # pages not covered yet are OCR'd (skip, never replace).
    data = request.get_json(silent=True) or {}
    pages_raw = str(data.get("ocr_pages") or request.form.get("ocr_pages", "")).strip()
    requested = parse_page_ranges(pages_raw)
    if requested is None:
        abort(400, "already OCR'd: pass ocr_pages to add pages (e.g. \"5-15\" or \"2-3, 4-9\")")
    n_pages = doc.get("n_pages") or page_count(Path(doc["pdf_path"]))
    wanted = sorted({p for s, e in requested for p in range(max(1, s), min(n_pages, e) + 1)})
    covered = {p["page"] for p in doc.get("pages", []) if p.get("items")}
    new_pages = [p for p in wanted if p not in covered]
    if not new_pages:
        abort(400, "all requested pages already OCR'd")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "done": 0, "total": 0}
    threading.Thread(target=_run_ocr, args=(job_id, doc, new_pages), daemon=True).start()
    return jsonify({"job_id": job_id, "skipped": len(wanted) - len(new_pages)})


def _run_ocr(job_id, doc, page_range=None):
    try:
        if not config.API_KEY:
            raise RuntimeError("UNSLOTH_API_KEY not set: set it to run GLM-OCR")
        pdf = Path(doc["pdf_path"])
        images, tmpdir = pdf_to_images(
            pdf, page_range=page_range if page_range is not None else doc.get("ocr_pages")
        )
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
        if page_range is not None:
            # Incremental merge: keep existing pages verbatim, add the new
            # ones, and preserve review state of already-existing items.
            merge_pages_into_doc(load_doc(doc["doc_id"]), page_range, results)
        else:
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
        for f in (d / doc_id).glob("*.json"):  # missing dir -> empty iterator
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


def _ocr_page_crop(doc, page, box, prompt=OCR_PROMPT):
    """Render a page region and OCR it with GLM-OCR; returns the text."""
    tmp = _render_crop(doc, page, box)
    try:
        return ocr_page(str(tmp), page_num=page, prompt=prompt)["text"]
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
    btype = data.get("type", "auto")
    if btype not in ("auto", "equation", "table", "text"):
        abort(400, "type must be auto|equation|table|text")  # checked before OCR runs
    try:
        page = int(data.get("page", 1))
        box = _parse_box(data)
    except ValueError:
        abort(400, "page, x, y, w, h required (fractions of the page, 0..1)")

    try:
        text = _ocr_page_crop(
            doc, page, box,
            prompt=EQUATION_PROMPT if btype == "equation" else OCR_PROMPT,
        )
    except ValueError as e:
        abort(404, str(e))
    except Exception as e:  # OCR/backend failure -> JSON error, not a crash
        return jsonify({"ok": False, "error": str(e)}), 502

    if btype == "auto":
        items_before = set(
            it["id"] for pg in doc.get("pages", []) for it in pg["items"]
        )
        added = append_bbox_items(doc, page, text)
        _attach_caption_to_first_table(doc, page, box, items_before)
    else:
        added = 0  # forced kind: one item, no auto-parsing/caption attach
        if text.strip():
            append_bbox_item(doc, page, text, btype)
            added = 1
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
        box = _parse_box(data)
    except ValueError:
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
    return render_template("index.html", docs=docs_list(), doc=doc, tab="ocr", page_model="ocr")


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
    if not apply_action(
        doc, item_id, action,
        content=data.get("content"),
        table_spans=data.get("table_spans"),
        eq_num=data.get("eq_num"),
        eq_letters=data.get("eq_letters"),
    ):
        abort(404, f"item {item_id} not found")
    save_doc(doc)
    return jsonify({"ok": True, "doc": doc})


@app.route("/item/<doc_id>/<item_id>/delete", methods=["POST"])
def item_delete(doc_id, item_id):
    """Remove an item from its page and drop both verified/rejected copies."""
    if (
        not re.fullmatch(r"[A-Za-z0-9._-]+", doc_id)
        or not re.fullmatch(r"[A-Za-z0-9._-]+", item_id)
    ):
        abort(404)
    doc = load_doc(doc_id)
    if doc is None:
        abort(404)
    removed = False
    for page in doc.get("pages", []):
        for item in page["items"]:
            if item["id"] == item_id:
                page["items"].remove(item)
                removed = True
                break
        if removed:
            break
    if not removed:
        abort(404, f"item {item_id} not found")
    for d in (VERIFIED_DIR, REJECTED_DIR):
        (d / doc_id / f"{item_id}.json").unlink(missing_ok=True)
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
    if action not in ("skip", "accept", "reject"):
        abort(400, "bulk action must be skip|accept|reject")
    targets = set(data.get("item_ids", []))
    updated = 0
    for page in doc.get("pages", []):
        for item in page["items"]:
            if item["id"] not in targets:
                continue  # not targeted
            if action == "skip" and item["status"] in ("verified", "rejected"):
                continue  # skip never touches finalized items
            target = {"accept": "verified", "reject": "rejected"}.get(action, "skipped")
            if item["status"] == target:
                continue  # already in the target state: nothing changes
            apply_action(doc, item["id"], action)
            updated += 1
    save_doc(doc)
    return jsonify({"ok": True, "updated": updated, "doc": doc})


# --- chat sessions + messages ---


@app.route("/api/chat/sessions", methods=["GET", "POST"])
def chat_sessions():
    if request.method == "GET":
        return jsonify({"sessions": sessions_list()})
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        abort(400, "name required")
    now = _now()
    s = {"id": uuid.uuid4().hex[:12], "name": name, "kb_id": None,
         "created_at": now, "updated_at": now, "messages": []}
    save_session(s)
    return jsonify(s), 201


@app.route("/api/chat/sessions/<sid>", methods=["GET", "PATCH", "DELETE"])
def chat_session(sid):
    s = load_session(sid)
    if s is None:
        abort(404)
    if request.method == "GET":
        return jsonify(s)
    if request.method == "DELETE":
        sid_path = session_path(sid)
        if sid_path is None:
            abort(404)
        sid_path.unlink(missing_ok=True)
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            abort(400, "name cannot be empty")
        s["name"] = name
    if "kb_id" in data:
        s["kb_id"] = data.get("kb_id") or None
    s["updated_at"] = _now()
    save_session(s)
    return jsonify(s)


@app.route("/api/chat/sessions/<sid>/messages", methods=["POST"])
def chat_message(sid):
    s = load_session(sid)
    if s is None:
        abort(404)
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    if not content:
        abort(400, "content required")
    developer = bool(data.get("developer"))
    now = _now()
    s["messages"].append({"role": "user", "content": content, "ts": now})
    try:
        answer, trace = orchestrator.answer_turn(
            content, s["messages"][:-1], s.get("kb_id"), MAX_CHAT_TOKENS)
    except RuntimeError as e:
        s["messages"].pop()  # failed turn: keep the session retryable, don't persist it
        return jsonify({"error": str(e)}), 502
    s["messages"].append({"role": "assistant", "content": answer, "ts": _now()})
    s["updated_at"] = _now()
    save_session(s)
    resp = {"answer": answer, "session": s}
    if developer:
        resp["trace"] = trace  # dev mode only: trace is live, never persisted
    return jsonify(resp)


# --- Unsloth RAG knowledge-base management ---


@app.route("/api/kb")
def kb_list():
    try:
        kbs = rag.list_kbs()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"knowledgeBases": [
        {"id": k["id"], "name": k["name"],
         "documentCount": k.get("documentCount", 0),
         "description": k.get("description")} for k in kbs]})


@app.route("/api/kb", methods=["POST"])
def kb_create():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        abort(400, "name required")
    try:
        kb = rag.create_kb(name, data.get("description") or None)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"id": kb["id"], "name": kb.get("name") or name}), 201


@app.route("/api/kb/<kb_id>", methods=["PATCH", "DELETE"])
def kb_update(kb_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", kb_id):
        abort(404)
    if request.method == "DELETE":
        try:
            rag.delete_kb(kb_id)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 502
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        abort(400, "name required")
    try:
        rag.rename_kb(kb_id, name)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"ok": True, "name": name})


def _kb_upload_docs(kb_id, doc_id):
    """Upload one verified doc (or "__all__") to a KB. Returns
    (uploaded_filenames, skipped). Raises RuntimeError on API failures."""
    if doc_id == "__all__":
        by_doc = rag.docs_from_verified(rag.VERIFIED_DIR)
        dirs = {d.name for d in rag.VERIFIED_DIR.iterdir() if d.is_dir()}
        uploaded = []
        for did, items in sorted(by_doc.items()):
            rag.upload_doc(kb_id, f"{did}.md", rag.render_markdown(items), False)
            uploaded.append(f"{did}.md")
        return uploaded, len(dirs - set(by_doc))
    items = rag.docs_for_doc(doc_id)
    if not items:
        abort(404, f"no verified items for {doc_id}")
    rag.upload_doc(kb_id, f"{doc_id}.md", rag.render_markdown(items), False)
    return [f"{doc_id}.md"], 0


@app.route("/api/kb/<kb_id>/upload", methods=["POST"])
def kb_upload(kb_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", kb_id):
        abort(404)
    data = request.get_json(silent=True) or {}
    doc_id = str(data.get("doc_id") or "").strip()
    if not doc_id:
        abort(400, 'doc_id required (or "__all__")')
    try:
        uploaded, skipped = _kb_upload_docs(kb_id, doc_id)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"uploaded": uploaded, "skipped": skipped})


# --- model management (one resident model at a time) ---


MODEL_NAMES = {"ocr": "GLM-OCR", "chat": "granite"}  # human labels for steps/toasts
# The active model-swap job id, so GET /api/model can report an in-flight job
# and any page load re-attaches progress (tabs are full page loads — the JS
# toast alone dies on navigation). Single-user, at most one model job at a time.
MODEL_JOB_ID = None


def _target_loaded():
    """The loaded model key ("ocr"/"chat"/None) via models.current_model()
    (suffix match — the backend may report a resolved local snapshot path).
    Raises RuntimeError on API failure."""
    loaded = models.current_model() or ""
    if not loaded:
        return None
    for key, path in models.MODELS.items():
        if Path(path).name in loaded:
            return key
    return None


@app.route("/api/model")
def model_status():
    try:
        loaded = models.current_model()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    key = None
    if loaded:
        for k, path in models.MODELS.items():
            if Path(path).name in loaded:
                key = k
    resp = {"loaded": loaded, "key": key, "job": None}
    if MODEL_JOB_ID and MODEL_JOB_ID in JOBS:
        j = JOBS[MODEL_JOB_ID]
        if j.get("status") == "running":
            resp["job"] = {"id": MODEL_JOB_ID, "status": "running",
                            "step": j.get("step", "")}
    return jsonify(resp)


@app.route("/api/model/load", methods=["POST"])
def model_load():
    global MODEL_JOB_ID
    data = request.get_json(silent=True) or {}
    key = str(data.get("model") or "")
    if key not in models.MODELS:
        abort(400, "model must be ocr|chat")
    try:
        current_key = _target_loaded()
        if current_key == key:
            return jsonify({"status": "done", "step": "already loaded"})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    job_id = uuid.uuid4().hex[:12]
    what = MODEL_NAMES[current_key] if current_key else "resident"
    JOBS[job_id] = {"status": "running", "step": f"unloading {what}"}
    MODEL_JOB_ID = job_id  # a new load supersedes any stale pointer
    threading.Thread(target=_model_job, args=(job_id, key, current_key), daemon=True).start()
    return jsonify({"job_id": job_id})


def _model_job(job_id, key, current_key):
    """Worker: unload whatever is resident, then load the target model.
    Unload ALWAYS precedes load (the backend is single-model; load would
    refuse or evict mid-flight otherwise). force_cancel_active kills
    non-cancellable in-flight generations (ocr_engine sends non-streaming
    calls)."""
    global MODEL_JOB_ID
    try:
        JOBS[job_id]["step"] = f"unloading {MODEL_NAMES[current_key] if current_key else 'resident'}"
        models.unload(models.MODELS[current_key] if current_key else None)  # no-op when nothing loaded
        JOBS[job_id]["step"] = f"loading {MODEL_NAMES[key]}"
        models.load(key)
        JOBS[job_id].update(status="done", step=f"loaded {MODEL_NAMES[key]}")
    except Exception as e:
        JOBS[job_id] = {"status": "error", "error": str(e)}
    finally:
        if MODEL_JOB_ID == job_id:
            MODEL_JOB_ID = None  # a later load already superseded it -> leave it


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")