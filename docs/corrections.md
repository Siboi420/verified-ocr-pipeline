# Plan Corrections — OCR Validation App (pre-build review)

## 1. Template structure (contradiction)

The plan says "single-file app + inline HTML" but references `templates/index.html`. Pick one — templates/ is cleaner for Flask with a real UI. Go with the template approach.

## 2. OCR must be async from day one

The plan says "synchronous for now, background job if slow." GLM-OCR takes 10-15s per page. A 15-page document = ~3 minutes. The server blocks during that time — no other pages can load, no items can be reviewed.

Fix: async from v1. Submit OCR job, return a job ID, poll for completion, render results. Use a simple thread + status check (not a full task queue, just `threading` + `{job_id: {"status": "running"|"done", "md_path": ...}}` dict in memory).

## 3. Import ocr_engine.py directly

The plan says "run ocr_engine.py functions" without specifying how. ocr_engine.py is cleanly importable — all functions are top-level, main() is guarded by `if __name__ == "__main__"`.

Do: `from ocr_engine import pdf_to_images, ocr_batch, assemble_markdown`
Don't: subprocess calls to `python3 ocr_engine.py ...`

## 4. Item ordering by page first, not globally

The plan says "Default sort: equation > table > text-with-inline-math > plain text." A global sort puts equations from page 10 before tables from page 1. This breaks the natural reading flow.

Fix: sort by (page_number, type_priority). The user reviews all items on page 1 first, then moves to page 2, etc. Within a page, equations first, then tables, then text-with-math, then plain text.

## 5. After-action navigation

After accepting/rejecting an item, what happens? Not defined in the plan, and it matters for flow.

Fix: after action, move to the next item of the same type on the same page. If none remain on this page, move to the next page's first priority item. Show a counter: "Item 3/7 on page 4."

## 6. Table editing UX

Editing a markdown table as raw text is error-prone (pipe alignment, escaping, line breaks). The user will make mistakes.

Fix: parse the markdown table into an editable HTML grid (rows x columns). Each cell is an `<input>` field. Reconstruct markdown from the grid on save. This mirrors how the user's Boon annotation tools work — visual editing, not raw text.

## 7. Page-to-item navigation

The plan has a "page selector" but doesn't define how items relate to pages in the UI flow.

Fix: default view = page-by-page. Show all items on page N, sorted by priority. The user works through items on the current page, then advances. Page selector allows jumping to any page. Item filter (equation/table/text) narrows within the current page only.

## 8. Text-with-inline-math handling

The plan correctly flags text-with-inline-math above plain text, but needs to clarify what "above" means. The user said "prose text doesn't need item-level review" — inline math in text is a corner case, not a regular review target.

Fix: flag text-with-math with a visual badge and filter option, but don't elevate it to the same priority as tables/equations. The default review flow should only show tables and equations. Text-with-math is available via a "Show inline math" toggle.

## 9. Config centralization

API_BASE, MODEL, and temp directory paths are currently in ocr_engine.py. The app will also need these.

Fix: create `config.py` in the project root with shared settings, read from env vars with sensible defaults. Both app.py and ocr_engine.py import from it.

```python
# config.py
import os
API_BASE = os.environ.get("UNSLOTH_API_BASE", "http://localhost:8888")
MODEL    = "ggml-org/GLM-OCR-GGUF"
UPLOAD_DIR = os.environ.get("VALIDATION_UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "validation", "uploads"))
```

## 10. Requirements

requirements.txt needs flask added. Current file only has requests and PyMuPDF.

Add: flask, waitress (production WSGI, optional but good practice).