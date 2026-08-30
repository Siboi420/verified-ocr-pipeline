"""End-to-end smoke test for the validation app (test client, no live OCR).

Covers PLAN verification cases 2-4 minus the actual GLM-OCR call:
  - load by paths -> items parsed, page PNG renders
  - accept (edited table) -> validation/verified/<item_id>.json
  - reject equation -> validation/rejected/
  - skip -> stays pending, status=skipped
  - upload without .md -> ocr_needed=true; /ocr returns job_id immediately;
    with UNSLOTH_API_KEY unset the job errors with a clear message
Run: python3 smoke_test.py
"""
import io
import json
import os
import re
import sys
from pathlib import Path

import pymupdf

REPO = Path(__file__).resolve().parent

MARKDOWN = """# Smoke
--- Page 1 ---
CHAPTER 5
5.3 The frame carries a moment:
$$
M_u = 1.2 M_d + 1.6 M_l
$$

Table 1.2—Member capacities
| Member | Capacity (kN) |
|--------|---------------|
| B1     | 245.3         |
| B2     | 189.7         |

Trailing prose with \\(\\phi = 0.9\\) inline.

--- Page 2 ---
$$
V_s = \\frac{A_v f_y d}{s}
$$
"""
EDITED_TABLE = "| Member | Capacity (kN) |\n|---|---|\n|B1|999.9|\n|B2|188.8|"


def check(cond, msg):
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        sys.exit(1)


def read_json(path):
    """Read a JSON file, crashing the test loudly if it is missing/corrupt."""
    try:
        return json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise AssertionError(f"cannot read {path}: {e}") from e


def main():
    # parse_page_range: valid single/range/whitespace, invalid forms
    import ocr_engine as ocr

    check(ocr.parse_page_range("5") == (5, 5), "parse_page_range '5' -> (5,5)")
    check(ocr.parse_page_range("1-3") == (1, 3), "parse_page_range '1-3' -> (1,3)")
    check(ocr.parse_page_range(" 2-4 ") == (2, 4), "parse_page_range strips whitespace")
    check(ocr.parse_page_range("") is None, "parse_page_range '' -> None")
    check(ocr.parse_page_range("x") is None, "parse_page_range 'x' -> None")
    check(ocr.parse_page_range("0-2") is None, "parse_page_range '0-2' -> None (start<1)")
    check(ocr.parse_page_range("3-1") is None, "parse_page_range '3-1' -> None (end<start)")
    check(ocr.parse_page_range("1-") is None, "parse_page_range '1-' -> None (dangling dash)")
    check(ocr.parse_page_ranges("2-3, 4-9") == [(2, 3), (4, 9)],
          "parse_page_ranges '2-3, 4-9' -> [(2,3),(4,9)]")
    check(ocr.parse_page_ranges("5") == [(5, 5)], "parse_page_ranges '5' -> [(5,5)]")
    check(ocr.parse_page_ranges("") is None, "parse_page_ranges '' -> None")
    check(ocr.parse_page_ranges("2-3,x") is None, "parse_page_ranges bad chunk -> None")
    check(ocr.parse_page_ranges("3-1") is None, "parse_page_ranges reversed -> None")
    # Fixture PDF (2 pages) so page_count and page PNG work.
    pdf_dir = REPO / "validation" / "uploads" / "smoke"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / "smoke.pdf"
    md_path = pdf_dir / "smoke.md"
    if not pdf_path.exists():
        doc = pymupdf.open()
        for _ in range(2):
            doc.new_page()
        doc.save(str(pdf_path))
        doc.close()
    md_path.write_text(MARKDOWN)

    os.environ.pop("UNSLOTH_API_KEY", None)  # force the no-key path deterministically

    import app as appmod  # pyright: ignore[reportMissingImports] — same-dir module

    client = appmod.app.test_client()

    # /load
    r = client.post("/load", data={"pdf_path": str(pdf_path), "md_path": str(md_path)})
    check(r.status_code == 302, "/load 302 redirect")
    doc_id = "smoke"
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    pages = pending["pages"]
    check(len(pages) == 2, "2 pages parsed")
    check([p["page"] for p in pages] == [1, 2], "page numbers 1,2")
    p1 = pages[0]["items"]
    check([i["type"] for i in p1] == ["equation", "table", "text", "text", "text"],
          "page 1 order: equation, table, text(with math), text, text (title merged)")
    check(p1[2]["has_inline_math"], "inline math flagged on trailing prose")
    check(pending["n_pages"] == 2, "n_pages from PyMuPDF = 2")
    table_item = p1[1]
    check(table_item.get("caption") == "Table 1.2—Member capacities",
          "table item carries auto-paired caption")
    check(table_item.get("table_number") == "1.2", "table item carries table_number")
    check(table_item.get("section") == "5.3" and table_item.get("chapter") == "5",
          "table item carries section=5.3, chapter=5 from the heading")
    check(p1[0].get("section") == "5.3" and p1[0].get("chapter") == "5",
          "equation item inherits section/chapter too")

    # /doc/<id>
    r = client.get(f"/doc/{doc_id}")
    html = r.get_data(as_text=True)
    check(r.status_code == 200 and 'id="main"' in html,
          "/doc/smoke renders review page")
    check("tex-chtml.js" in html and "tex-svg.js" not in html,
          "review page uses MathJax CHTML")
    check('id="captionEngine"' in html, "review page has caption engine select")
    check('id="onlyOcr"' in html, "review page has OCR'd-only page filter")
    check('id="bulkAcceptMath"' in html, "review page has inline-math bulk accept button")
    check('id="bulkAcceptText"' in html, "review page has text bulk accept button")

    # page PNG
    r = client.get(f"/page/{doc_id}/1.png")
    check(r.status_code == 200 and r.mimetype == "image/png" and len(r.data) > 1000,
          "page PNG served")

    # accept edited table -> verified/
    table_id = p1[1]["id"]
    r = client.post(f"/item/{doc_id}/{table_id}/action",
                    json={"action": "accept", "content": EDITED_TABLE})
    check(r.status_code == 200, "accept edited table 200")
    verified = read_json(REPO / "validation" / "verified" / f"{table_id}.json")
    check(verified["content"] == EDITED_TABLE, "verified JSON holds edited markdown")
    check(verified["type"] == "table" and verified["page"] == 1, "verified metadata correct")
    check(verified.get("caption") == "Table 1.2—Member capacities",
          "verified JSON carries caption")
    check(verified.get("table_number") == "1.2", "verified JSON carries table_number")
    check(verified.get("section") == "5.3" and verified.get("chapter") == "5",
          "verified JSON carries section/chapter from the item")
    check(verified.get("source_name") == "smoke.pdf", "verified JSON carries source_name")
    check("# OCR:" not in verified["content"], "verified content has no # OCR: title")
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    check(next(i for i in pending["pages"][0]["items"] if i["id"] == table_id)["status"] == "verified",
          "pending doc marks item verified")

    # finalized items can be flipped: verified -> rejected -> verified, copy moves
    r = client.post(f"/item/{doc_id}/{table_id}/action", json={"action": "reject"})
    check(r.status_code == 200, "flip verified->rejected 200")
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    check(next(i for i in pending["pages"][0]["items"] if i["id"] == table_id)["status"] == "rejected",
          "flip updates status to rejected")
    check((REPO / "validation" / "rejected" / f"{table_id}.json").exists(),
          "flip writes rejected copy")
    check(not (REPO / "validation" / "verified" / f"{table_id}.json").exists(),
          "flip removes stale verified copy")
    r = client.post(f"/item/{doc_id}/{table_id}/action", json={"action": "accept"})
    check(r.status_code == 200, "flip rejected->verified 200")
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    check(next(i for i in pending["pages"][0]["items"] if i["id"] == table_id)["status"] == "verified",
          "flip back restores verified")

    # reject equation -> rejected/
    eq_id = p1[0]["id"]
    r = client.post(f"/item/{doc_id}/{eq_id}/action", json={"action": "reject"})
    check(r.status_code == 200 and (REPO / "validation" / "rejected" / f"{eq_id}.json").exists(),
          "reject equation -> rejected/")

    # skip -> stays pending, status=skipped
    text_id = p1[2]["id"]
    r = client.post(f"/item/{doc_id}/{text_id}/action", json={"action": "skip"})
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    it = next(i for i in pending["pages"][0]["items"] if i["id"] == text_id)
    check(r.status_code == 200 and it["status"] == "skipped", "skip keeps item pending/skipped")

    # accept a text item: OCR boilerplate is cleaned out of the exported content
    text_payload = "# OCR: smoke.md\n\nCODE\n\nThis is the frame capacity prose.\n\n**Commentary**\n"
    r = client.post(f"/item/{doc_id}/{text_id}/action",
                    json={"action": "accept", "content": text_payload})
    check(r.status_code == 200, "accept text item 200")
    tv = read_json(REPO / "validation" / "verified" / f"{text_id}.json")
    # pi-lens-ignore: no-identity-operator-on-literals
    check(tv["content"] == "\n\nThis is the frame capacity prose.\n",
          "accepted text export drops # OCR:/CODE/**Commentary** lines")
    check(tv.get("section") == "5.3" and tv.get("source_name") == "smoke.pdf",
          "text export carries section/source_name")

    # bulk pass-for-now over remaining pending items on the doc
    r = client.post("/bulk", json={"doc_id": doc_id, "action": "skip",
                                   "item_ids": [i["id"] for pg in pending["pages"] for i in pg["items"]]})
    check(r.status_code == 200 and r.get_json()["updated"] == 3, "bulk skip updates 3 (i4, i5, page2 equation)")

    # bbox crop: pure append helper adds a table with a unique bbox id
    probe = {"doc_id": doc_id, "pages": []}
    n = appmod.append_bbox_items(probe, 1, "|A|B|\n|---|---|\n|1|2|")
    check(n == 1 and probe["pages"][0]["page"] == 1
          and probe["pages"][0]["items"][0]["type"] == "table"
          and "bbox" in probe["pages"][0]["items"][0]["id"],
          "append_bbox_items adds table with bbox id")

    # bbox route input validation (no live OCR call)
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 1})
    check(r.status_code == 400, "bbox OCR rejects missing coords")
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 1, "x": "nan", "y": 0, "w": .5, "h": .5})
    check(r.status_code == 400, "bbox OCR rejects NaN coords")
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 99, "x": 0, "y": 0, "w": .5, "h": .5})
    check(r.status_code == 404, "bbox OCR rejects out-of-range page")

    # caption route input validation (no live OCR call)
    r = client.post(f"/item/{doc_id}/{table_id}/caption", json={"x": .2, "y": .2})
    check(r.status_code == 400, "caption route rejects missing coords")
    r = client.post(f"/item/{doc_id}/{table_id}/caption", json={"page": 99, "x": 0, "y": 0, "w": .5, "h": .5})
    check(r.status_code == 404, "caption route rejects out-of-range page")
    r = client.post(f"/item/{doc_id}/{eq_id}/caption", json={"page": 1, "x": 0, "y": 0, "w": .5, "h": .5})
    check(r.status_code == 400, "caption route rejects non-table item")
    r = client.post(f"/item/{doc_id}/{table_id}/caption",
                    json={"page": 1, "x": 0, "y": 0, "w": .5, "h": .5, "engine": "wat"})
    check(r.status_code == 400, "caption route rejects invalid engine")

    # upload without .md -> async OCR job; key unset -> clear error
    try:
        pdf_bytes = open(pdf_path, "rb").read()
    except OSError as e:
        raise AssertionError(f"cannot read {pdf_path}: {e}") from e
    # native form submit (Accept: text/html) -> redirect to /doc/<id>?ocr=1
    r = client.post("/upload", data={"pdf": (io.BytesIO(pdf_bytes), "smoke.pdf")},
                    headers={"Accept": "text/html"})
    check(r.status_code == 302 and r.headers.get("Location", "").endswith("?ocr=1"),
          "html upload without md -> 302 /doc/smoke?ocr=1")
    # AJAX upload -> JSON ocr_needed (existing behavior)
    r = client.post("/upload", data={"pdf": (io.BytesIO(pdf_bytes), "smoke.pdf")})
    check(r.status_code == 200, "upload 200")
    up = r.get_json()
    check(up["doc_id"] == "smoke" and up["ocr_needed"], "upload without md -> ocr_needed")

    # upload with an ocr_pages range -> stored on the pending doc JSON
    r = client.post("/upload", data={"pdf": (io.BytesIO(pdf_bytes), "range.pdf"),
                                     "ocr_pages": "1-2"})
    check(r.status_code == 200, "upload range.pdf 200")
    rng = r.get_json()
    check(rng["doc_id"] == "range", "range upload doc_id = range")
    rg_doc = read_json(REPO / "validation" / "pending" / "range.json")
    check(rg_doc.get("ocr_pages") == [[1, 2]], "ocr_pages stored as [[1,2]]")
    check(rg_doc.get("pages") == [], "range doc starts with no items")

    # multiple comma-separated ranges -> stored as a list of [start, end] pairs
    r = client.post("/upload", data={"pdf": (io.BytesIO(pdf_bytes), "multi.pdf"),
                                     "ocr_pages": "2-3, 4-9"})
    check(r.status_code == 200, "upload multi-range 200")
    multi_doc = read_json(REPO / "validation" / "pending" / "multi.json")
    check(multi_doc.get("ocr_pages") == [[2, 3], [4, 9]],
          "multi-range stored as [[2,3],[4,9]]")
    client.post("/doc/multi/discard")

    # pdf_to_images accepts a list of ranges (merged, clamped to the PDF)
    imgs, _ = appmod.pdf_to_images(str(pdf_path), page_range=[[1, 1], [2, 2]])
    check([n for n, _ in imgs] == [1, 2], "pdf_to_images merges multiple ranges")
    imgs, _ = appmod.pdf_to_images(str(pdf_path), page_range=[[2, 2]])
    check([n for n, _ in imgs] == [2], "pdf_to_images nested single range")

    # invalid ocr_pages -> 400, nothing stored
    r = client.post("/upload", data={"pdf": (io.BytesIO(pdf_bytes), "range.pdf"),
                                     "ocr_pages": "3-1"})
    check(r.status_code == 400, "invalid ocr_pages -> 400")
    r = client.post("/upload", data={"pdf": (io.BytesIO(pdf_bytes), "range.pdf"),
                                     "ocr_pages": "abc"})
    check(r.status_code == 400, "non-numeric ocr_pages -> 400")
    rg_doc = read_json(REPO / "validation" / "pending" / "range.json")
    check(rg_doc.get("ocr_pages") == [[1, 2]], "failed uploads leave stored range intact")

    # markdown + ocr_pages -> markdown wins, range not stored
    r = client.post("/upload", data={"pdf": (io.BytesIO(pdf_bytes), "range.pdf"),
                                     "md": (io.BytesIO(MARKDOWN.encode()), "range.md"),
                                     "ocr_pages": "1"})
    check(r.status_code == 200 and not r.get_json()["ocr_needed"],
          "upload with md + ocr_pages -> no ocr needed")
    rg_doc = read_json(REPO / "validation" / "pending" / "range.json")
    check("ocr_pages" not in rg_doc, "md upload does not store ocr_pages")
    client.post(f"/doc/{rng['doc_id']}/discard")

    r = client.post(f"/ocr/{up['doc_id']}")
    check(r.status_code == 200, "/ocr returns immediately")
    job_id = r.get_json()["job_id"]
    import time
    st = {"status": "running"}
    for _ in range(50):
        st = client.get(f"/jobs/{job_id}").get_json()
        if st["status"] != "running":
            break
        time.sleep(0.1)
    check(st["status"] == "error" and "UNSLOTH_API_KEY" in st.get("error", ""),
          f"no-key OCR job fails clearly: {st.get('error', '')[:60]}")

    # GET / still lists the loaded doc
    r = client.get("/")
    idx = r.get_data(as_text=True)
    check(r.status_code == 200 and "smoke" in idx, "/ lists documents")
    check("tex-chtml.js" in idx and "tex-svg.js" not in idx,
          "index uses MathJax CHTML")

    # discard document: pending record + verified/rejected item copies removed
    r = client.post(f"/doc/{doc_id}/discard")
    check(r.status_code == 200 and not (REPO / "validation" / "pending" / f"{doc_id}.json").exists(),
          "discard removes pending record")
    check(not any((REPO / "validation" / "verified").glob(f"{doc_id}-*.json")),
          "discard cleans verified items")

    print("\nsmoke: all checks passed")


if __name__ == "__main__":
    main()