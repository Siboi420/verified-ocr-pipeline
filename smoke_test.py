"""End-to-end smoke test for the validation app (test client, no live OCR).

Covers PLAN verification cases 2-4 minus the actual GLM-OCR call:
  - load by paths -> items parsed, page PNG renders
  - accept (edited table) -> validation/verified/<doc_id>/<item_id>.json
  - reject equation -> validation/rejected/<doc_id>/
  - skip -> stays pending, status=skipped
  - upload without .md -> ocr_needed=true; /ocr returns job_id immediately;
    with UNSLOTH_API_KEY unset the job errors with a clear message
  - equation key editing: explicit eq_num/eq_letters (None = untouched, "" = cleared)
  - draw-box type forcing (auto/equation/table/text) + append_bbox_item kinds
  - per-item delete (item + verified/rejected copies removed)
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

    # --- manual order: POST order sets it, page reorders, validation 400s ---
    eq_id0 = p1[0]["id"]  # equation (doc position 3 on page 1)
    r = client.post(f"/item/{doc_id}/{eq_id0}/order", json={"order": 1.5})
    check(r.status_code == 200, "order route 200")
    out1 = r.get_json()["doc"]["pages"][0]["items"]
    check(next(i for i in out1 if i["id"] == eq_id0)["order"] == 1.5,
          "order persisted on the item")
    by_order = sorted(out1, key=lambda i: (i.get("order") is None, i.get("order", 0)))
    check(by_order[0]["id"] != eq_id0 and by_order[1]["id"] == eq_id0,
          "sorting by order places the reordered equation between orders 1 and 2")
    check(client.post(f"/item/{doc_id}/{eq_id0}/order", json={}).status_code == 400,
          "order missing -> 400")
    check(client.post(f"/item/{doc_id}/{eq_id0}/order", json={"order": "abc"}).status_code == 400,
          "order non-numeric -> 400")
    check(client.post(f"/item/{doc_id}/{eq_id0}/order", json={"order": "nan"}).status_code == 400,
          "order NaN -> 400")
    check(client.post(f"/item/{doc_id}/{eq_id0}/order", json={"order": 1e999}).status_code == 400,
          "order inf -> 400")
    check(client.post(f"/item/{doc_id}/nope/order", json={"order": 1}).status_code == 404,
          "order unknown item -> 404")

    # order flows into the verified/rejected JSON payloads; a fresh parse
    # re-stamps document order independent of the manual edit (the route
    # touches the doc JSON only)
    r = client.post(f"/item/{doc_id}/{eq_id0}/action", json={"action": "accept"})
    ev = read_json(REPO / "validation" / "verified" / "smoke" / f"{eq_id0}.json")
    check(r.status_code == 200 and ev.get("order") == 1.5,
          "accepted JSON carries the item's order")
    r = client.post(f"/item/{doc_id}/{eq_id0}/action", json={"action": "reject"})
    rv = read_json(REPO / "validation" / "rejected" / "smoke" / f"{eq_id0}.json")
    check(r.status_code == 200 and rv.get("order") == 1.5,
          "rejected JSON carries the order too (flip back for the editor flow)")
    fresh = appmod.parse_document(md_path.read_text(), doc_id)
    check(next(i for i in fresh[0]["items"] if i["id"] == eq_id0)["order"] == 3,
          "fresh parse re-stamps document order (manual edit is doc-JSON only)")

    # _ensure_order: a legacy doc (no order fields, no stamp marker) gets
    # document order without losing review state; bbox-only items tail the
    # page; deleted items are not resurrected
    edoc = "ensurex"
    efresh = appmod.parse_document(md_path.read_text(), edoc)  # ids ensurex-p1-i*: orders 3,4,5,1,2
    legacy = {
        "doc_id": edoc,
        "md_path": str(md_path),
        "pdf_path": str(pdf_path),
        "pages": [{"page": 1, "items": [
            {k: v for k, v in it.items() if k != "order"}
            for it in efresh[0]["items"]
            if not it["id"].endswith("-i3")  # i3 deleted
        ]}],
    }
    keep5 = next(it for it in legacy["pages"][0]["items"] if it["id"].endswith("-i5"))
    keep5["status"] = "verified"
    keep5["content"] = "EDITED PROSE"
    legacy["pages"][0]["items"].append({
        "id": f"{edoc}-p1-bboxabcd", "type": "text",
        "content": "drawn crop", "status": "pending"})
    appmod._ensure_order(legacy)
    lout = read_json(REPO / "validation" / "pending" / f"{edoc}.json")
    lorders = {it["id"]: it.get("order") for it in lout["pages"][0]["items"]}
    check(lout.get("_order_stamped"), "_ensure_order persists the stamp marker")
    check(all(o is not None for o in lorders.values()),
          "_ensure_order stamps order onto every existing item")
    check(lorders[f"{edoc}-p1-i1"] == 3 and lorders[f"{edoc}-p1-i5"] == 2,
          "_ensure_order reuses the fresh parse's document orders")
    check(not any(i_.endswith("-i3") for i_ in lorders),
          "_ensure_order does not resurrect deleted items")
    check(lorders[f"{edoc}-p1-bboxabcd"] == max(lorders.values()),
          "bbox-only item tails the page order")
    k5 = next(it for it in lout["pages"][0]["items"] if it["id"].endswith("-i5"))
    check(k5["status"] == "verified" and k5["content"] == "EDITED PROSE",
          "_ensure_order preserves review state (status / edited content)")
    client.post(f"/doc/{edoc}/discard")
    check(not (REPO / "validation" / "pending" / f"{edoc}.json").exists(),
          "ensurex doc discarded")

    # accept edited table -> verified/
    table_id = p1[1]["id"]
    r = client.post(f"/item/{doc_id}/{table_id}/action",
                    json={"action": "accept", "content": EDITED_TABLE})
    check(r.status_code == 200, "accept edited table 200")
    verified = read_json(REPO / "validation" / "verified" / "smoke" / f"{table_id}.json")
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
    check((REPO / "validation" / "rejected" / "smoke" / f"{table_id}.json").exists(),
          "flip writes rejected copy")
    check(not (REPO / "validation" / "verified" / "smoke" / f"{table_id}.json").exists(),
          "flip removes stale verified copy")
    r = client.post(f"/item/{doc_id}/{table_id}/action", json={"action": "accept"})
    check(r.status_code == 200, "flip rejected->verified 200")
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    check(next(i for i in pending["pages"][0]["items"] if i["id"] == table_id)["status"] == "verified",
          "flip back restores verified")

    # table merge info (table_spans) persists through accept and re-editing
    span_md = "| Specimen | f_c (MPa) |\n|---|---|\n| A-1 | 30 |\n| A-1 | 32 |"
    r = client.post(f"/item/{doc_id}/{table_id}/action",
                    json={"action": "accept", "content": span_md,
                          "table_spans": [{"r": 2, "c": 0, "rs": 2, "cs": 1}]})
    check(r.status_code == 200, "accept table with table_spans 200")
    verified = read_json(REPO / "validation" / "verified" / "smoke" / f"{table_id}.json")
    check(verified.get("table_spans") == [{"r": 2, "c": 0, "rs": 2, "cs": 1}],
          "verified JSON carries table_spans")
    check(verified["content"] == span_md, "verified content keeps markdown table")
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    check(next(i for i in pending["pages"][0]["items"] if i["id"] == table_id)["table_spans"]
          == [{"r": 2, "c": 0, "rs": 2, "cs": 1}],
          "pending item carries table_spans")

    # editing an already-verified table (accept with new content+spans) updates it
    r = client.post(f"/item/{doc_id}/{table_id}/action",
                    json={"action": "accept", "content": "| X |\n|---|\n| y |",
                          "table_spans": []})
    verified = read_json(REPO / "validation" / "verified" / "smoke" / f"{table_id}.json")
    check(r.status_code == 200 and verified["content"].startswith("| X |")
          and verified.get("table_spans") == [],
          "re-accepting a verified table with new content updates it")

    # equation key editing: eq_num/eq_letters explicit, never re-derived
    eq_id = p1[0]["id"]
    eq_content = p1[0]["content"]
    r = client.post(f"/item/{doc_id}/{eq_id}/action",
                    json={"action": "accept", "content": eq_content,
                          "eq_num": "22.5.1.10", "eq_letters": "a"})
    check(r.status_code == 200, "accept equation with eq key 200")
    ev = read_json(REPO / "validation" / "verified" / "smoke" / f"{eq_id}.json")
    check(ev.get("eq_num") == "22.5.1.10" and ev.get("eq_letters") == "a",
          "verified JSON carries eq_num/eq_letters")
    # re-accept with content only: the key survives (the bug fix)
    r = client.post(f"/item/{doc_id}/{eq_id}/action",
                    json={"action": "accept", "content": eq_content})
    check(r.status_code == 200, "re-accept equation 200")
    ev = read_json(REPO / "validation" / "verified" / "smoke" / f"{eq_id}.json")
    check(ev.get("eq_num") == "22.5.1.10",
          "re-accept with content only preserves the eq key")
    # empty eq_num clears the key (item-level; export drops the coupled eq_letters too)
    r = client.post(f"/item/{doc_id}/{eq_id}/action",
                    json={"action": "accept", "content": eq_content, "eq_num": ""})
    ev = read_json(REPO / "validation" / "verified" / "smoke" / f"{eq_id}.json")
    check(r.status_code == 200 and ev.get("eq_num") is None, "empty eq_num clears the key")
    pd0 = read_json(REPO / "validation" / "pending" / "smoke.json")
    pending_eq = next(i for i in pd0["pages"][0]["items"] if i["id"] == eq_id)
    check("eq_num" not in pending_eq and pending_eq.get("eq_letters") == "a",
          "item keeps eq_letters when only eq_num is cleared")
    # reject equation -> rejected/
    r = client.post(f"/item/{doc_id}/{eq_id}/action", json={"action": "reject"})
    check(r.status_code == 200 and (REPO / "validation" / "rejected" / "smoke" / f"{eq_id}.json").exists(),
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
    tv = read_json(REPO / "validation" / "verified" / "smoke" / f"{text_id}.json")
    # pi-lens-ignore: no-identity-operator-on-literals
    check(tv["content"] == "\n\nThis is the frame capacity prose.\n",
          "accepted text export drops # OCR:/CODE/**Commentary** lines")
    check(tv.get("section") == "5.3" and tv.get("source_name") == "smoke.pdf",
          "text export carries section/source_name")

    # bulk reject flips an accepted item (finalized items are bulk-mutable for accept/reject)
    r = client.post("/bulk", json={"doc_id": doc_id, "action": "reject",
                                   "item_ids": [table_id]})
    check(r.status_code == 200 and r.get_json()["updated"] == 1, "bulk reject flips accepted item")
    pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    check(next(i for i in pending["pages"][0]["items"] if i["id"] == table_id)["status"] == "rejected",
          "bulk reject updates status")
    check(not (REPO / "validation" / "verified" / "smoke" / f"{table_id}.json").exists()
          and (REPO / "validation" / "rejected" / "smoke" / f"{table_id}.json").exists(),
          "bulk reject moves copies")

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

    # append_bbox_item: forced kinds (equation key capture, text math flag, inheritance)
    probe2 = {"doc_id": "probe", "pages": [{"page": 1, "items": [
        {"id": "prev", "type": "text", "content": "x", "chapter": "5", "section": "5.3"}]}]}
    eitem = appmod.append_bbox_item(probe2, 1, "(a) $$E = m c^2$$ (22.5.1.10a)", "equation")
    check(eitem["eq_num"] == "22.5.1.10a" and eitem["eq_letters"] == "a",
          "append_bbox_item equation captures eq_num/eq_letters")
    check(eitem.get("order") == 1,
          "append_bbox_item assigns tail order (page had 1 item, no orders)")
    check(eitem["chapter"] == "5" and eitem["section"] == "5.3",
          "append_bbox_item inherits chapter/section from the latest item")
    titem = appmod.append_bbox_item(probe2, 1, "The factor \\(\\phi = 0.9\\) governs.", "text")
    tplain = appmod.append_bbox_item(probe2, 1, "Plain prose.", "text")
    check(titem["has_inline_math"] and not tplain["has_inline_math"],
          "append_bbox_item text flags inline math")
    check(titem.get("order") == 2 and tplain.get("order") == 3,
          "append_bbox_item orders increment at the page tail")
    check(appmod.append_bbox_item(probe2, 1, "|A|\n|---|\n|1|", "table")["type"] == "table",
          "append_bbox_item forced table kind")

    # equation/text kinds strip the HTML <table> wrapper GLM sometimes emits,
    # keeping the math/content (and a trailing eq key still captures)
    art = ('<table><thead><tr><th>$T_{n}$</th><th>$\\frac{2A_{o}A_{\\ell}f_{y}}{p_{h}}$</th>'
           '<th>$\\tan \\theta$</th></tr></thead><tbody><tr></tbody></table>')
    eq = appmod.append_bbox_item(probe2, 1, art + " (22.5.1.10a)", "equation")
    check(eq["content"] == "$T_{n}$ $\\frac{2A_{o}A_{\\ell}f_{y}}{p_{h}}$ $\\tan \\theta$ (22.5.1.10a)"
          and eq.get("eq_num") == "22.5.1.10a",
          "equation bbox strips HTML table wrapper, keeps math + eq key")
    tx = appmod.append_bbox_item(probe2, 1, art, "text")
    check(tx["content"] == "$T_{n}$ $\\frac{2A_{o}A_{\\ell}f_{y}}{p_{h}}$ $\\tan \\theta$"
          and tx["has_inline_math"],
          "text bbox also strips HTML table wrapper, flags inline math")
    tb = appmod.append_bbox_item(probe2, 1, art, "table")
    check(tb["content"] == art, "forced table bbox keeps raw wrapper text")

    # bbox route input validation (no live OCR call)
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 1})
    check(r.status_code == 400, "bbox OCR rejects missing coords")
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 1, "x": "nan", "y": 0, "w": .5, "h": .5})
    check(r.status_code == 400, "bbox OCR rejects NaN coords")
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 99, "x": 0, "y": 0, "w": .5, "h": .5})
    check(r.status_code == 404, "bbox OCR rejects out-of-range page")
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 1, "x": 0, "y": 0, "w": .5, "h": .5, "type": "wat"})
    check(r.status_code == 400, "bbox OCR rejects invalid type before OCR")
    r = client.post(f"/bbox_ocr/{doc_id}", json={"page": 1, "x": 0, "y": 0, "w": .5, "h": .5, "type": "equation"})
    check(r.status_code == 502 and "UNSLOTH_API_KEY" in r.get_json().get("error", ""),
          "valid forced type passes validation, reaches OCR (no-key error)")

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

    # delete item: restore the parsed doc, accept then delete (copy removed)
    r = client.post("/load", data={"pdf_path": str(pdf_path), "md_path": str(md_path)})
    check(r.status_code == 302, "reload for delete test 302")
    dl_pending = read_json(REPO / "validation" / "pending" / "smoke.json")
    dl_id = dl_pending["pages"][0]["items"][0]["id"]
    client.post(f"/item/{doc_id}/{dl_id}/action", json={"action": "accept"})
    check((REPO / "validation" / "verified" / "smoke" / f"{dl_id}.json").exists(),
          "verified copy exists before delete")
    r = client.post(f"/item/{doc_id}/{dl_id}/delete", json={})
    check(r.status_code == 200, "delete item 200")
    out = r.get_json()
    check(not any(dl_id in [i["id"] for i in pg.get("items", [])]
                  for pg in out["doc"].get("pages", [])),
          "delete removes item from the pending doc")
    check(not (REPO / "validation" / "verified" / "smoke" / f"{dl_id}.json").exists()
          and not (REPO / "validation" / "rejected" / "smoke" / f"{dl_id}.json").exists(),
          "delete removes verified/rejected copies")
    r = client.post(f"/item/{doc_id}/does-not-exist/delete", json={})
    check(r.status_code == 404, "delete unknown item 404")

    # incremental OCR: a doc with items only on pages 1-2 of a 3-page PDF
    merge_dir = REPO / "validation" / "uploads" / "merge"
    merge_dir.mkdir(parents=True, exist_ok=True)
    m_pdf = merge_dir / "merge.pdf"
    m_md = merge_dir / "merge.md"
    if not m_pdf.exists():
        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page()
        doc.save(str(m_pdf))
        doc.close()
    m_md.write_text(MARKDOWN)  # pages 1-2 only
    r = client.post("/load", data={"pdf_path": str(m_pdf), "md_path": str(m_md)})
    check(r.status_code == 302, "/load merge.pdf 302")
    mg = read_json(REPO / "validation" / "pending" / "merge.json")
    check(mg["n_pages"] == 3 and [p["page"] for p in mg["pages"]] == [1, 2],
          "merge doc: 3-page PDF, items on pages 1-2 only")

    # no range on a doc with items -> actionable 400 naming ocr_pages
    r = client.post("/ocr/merge", json={})
    check(r.status_code == 400 and "ocr_pages" in r.get_data(as_text=True),
          "/ocr on doc with items, no range -> 400 naming ocr_pages")
    # invalid range -> 400
    r = client.post("/ocr/merge", json={"ocr_pages": "3-1"})
    check(r.status_code == 400, "/ocr invalid range -> 400")
    r = client.post("/ocr/merge", json={"ocr_pages": "abc"})
    check(r.status_code == 400, "/ocr non-numeric range -> 400")
    # fully inside already-covered pages -> 400, no job started
    r = client.post("/ocr/merge", json={"ocr_pages": "1-2"})
    check(r.status_code == 400 and "already OCR" in r.get_data(as_text=True),
          "/ocr all-covered range -> 400 already OCR'd")
    # uncovered page -> 200 + job_id (async job itself fails: key unset)
    r = client.post("/ocr/merge", json={"ocr_pages": "3"})
    check(r.status_code == 200 and r.get_json().get("job_id"),
          "/ocr uncovered page -> 200 with job_id")
    check(r.get_json().get("skipped") == 0, "uncovered-only range -> skipped 0")
    # mixed covered + uncovered -> skipped counts the already-covered page
    r = client.post("/ocr/merge", json={"ocr_pages": "2-3"})
    check(r.status_code == 200 and r.get_json().get("skipped") == 1,
          "mixed range -> 200, skipped=1 for covered page 2")
    # form-field fallback (no JSON body) accepted too
    r = client.post("/ocr/merge", data={"ocr_pages": "3"})
    check(r.status_code == 200 and r.get_json().get("job_id"),
          "form-field ocr_pages accepted")
    # range tail beyond the PDF clamps to n_pages; page 3 still uncovered
    r = client.post("/ocr/merge", json={"ocr_pages": "3-9"})
    check(r.status_code == 200 and r.get_json().get("skipped") == 0,
          "range clamped to PDF end -> 200, skipped 0 (page 3 new)")
    # range fully beyond the PDF -> empty after clamp -> 400, no job
    r = client.post("/ocr/merge", json={"ocr_pages": "9-15"})
    check(r.status_code == 400, "range beyond PDF -> 400 after clamp")

    # merge helper unit tests (no PDF/OCR involved)
    def page_order(md):
        return re.findall(r"^--- Page (\d+) ---", md, re.M)

    def block(md, n):
        tail = re.split(rf"^--- Page {n} ---\s*", md, flags=re.M)[1]
        return re.split(r"^--- Page \d+ ---\s*", tail, flags=re.M)[0].strip()

    md12 = MARKDOWN
    md3 = appmod.assemble_markdown(
        [{"page": 3, "path": "p3.png", "text": "PAGE THREE", "truncated": False}],
        source_name="")
    merged = appmod.merge_ocr_markdown(md12, md3)
    check(page_order(merged) == ["1", "2", "3"], "merge appends page 3 in order 1,2,3")
    check("# OCR:" not in merged and block(merged, 1) == block(md12, 1),
          "merge keeps page 1 block, no duplicated header")
    md2b = appmod.assemble_markdown(
        [{"page": 2, "path": "p2.png", "text": "REPLACED TWO", "truncated": False}],
        source_name="")
    merged2 = appmod.merge_ocr_markdown(md12, md2b)
    check(page_order(merged2) == ["1", "2"] and block(merged2, 2) == "REPLACED TWO"
          and block(merged2, 1) == block(md12, 1),
          "merge replaces page 2 block, page 1 untouched")
    merged3 = appmod.merge_ocr_markdown("# OCR: merge.pdf\n" + md12, md3)
    check(merged3.startswith("# OCR: merge.pdf") and page_order(merged3) == ["1", "2", "3"],
          "merge preserves the # OCR: header")

    # merge_pages_into_doc (no live OCR): review state survives an incremental
    # merge — page 1 item stays verified, page 3 items arrive pending.
    mer = read_json(REPO / "validation" / "pending" / "merge.json")
    keep_id = mer["pages"][0]["items"][0]["id"]
    r = client.post(f"/item/merge/{keep_id}/action", json={"action": "accept"})
    check(r.status_code == 200, "accept page-1 item before incremental merge")
    mer = read_json(REPO / "validation" / "pending" / "merge.json")
    check(next(i for i in mer["pages"][0]["items"] if i["id"] == keep_id)["status"] == "verified",
          "item verified before merge")
    # a manually-edited order survives the incremental merge (restore copies
    # whole item dicts by id)
    keep_item = next(i for i in mer["pages"][0]["items"] if i["id"] == keep_id)
    keep_item["order"] = 99.5
    (REPO / "validation" / "pending" / "merge.json").write_text(json.dumps(mer))
    mer = read_json(REPO / "validation" / "pending" / "merge.json")
    check(next(i for i in mer["pages"][0]["items"] if i["id"] == keep_id)["order"] == 99.5,
          "manual order persisted before merge")
    appmod.merge_pages_into_doc(
        mer, [3],
        [{"page": 3, "path": "p3.png", "text": "PAGE THREE", "truncated": False}])
    mer = read_json(REPO / "validation" / "pending" / "merge.json")
    p1 = mer["pages"][0]
    kept = next(i for i in p1["items"] if i["id"] == keep_id)
    check(kept["status"] == "verified" and kept["content"] == p1["items"][0]["content"]
          and kept.get("order") == 99.5,
          "page-1 review state (incl. manual order) preserved across incremental merge")
    p3 = next(p for p in mer["pages"] if p["page"] == 3)
    check(p3["items"] and all(i["status"] == "pending" for i in p3["items"]),
          "new page 3 items arrive pending")
    check([p["page"] for p in mer["pages"]] == [1, 2, 3],
          "merged doc pages in order 1,2,3")

    # bbox-style items (page-dict-only, not in md) survive an incremental merge
    mer = read_json(REPO / "validation" / "pending" / "merge.json")
    bbox_id = f"merge-p1-bbox{os.urandom(4).hex()}"
    mer["pages"][0]["items"].append({
        "id": bbox_id, "type": "text", "content": "drawn crop",
        "status": "verified", "page": 1})
    (REPO / "validation" / "pending" / "merge.json").write_text(json.dumps(mer))
    appmod.merge_pages_into_doc(
        read_json(REPO / "validation" / "pending" / "merge.json"), [4],
        [{"page": 4, "path": "p4.png", "text": "PAGE FOUR", "truncated": False}])
    mer = read_json(REPO / "validation" / "pending" / "merge.json")
    check(bbox_id in [i["id"] for i in mer["pages"][0]["items"]]
          and next(i for i in mer["pages"][0]["items"] if i["id"] == bbox_id)["status"] == "verified",
          "drawn-box item survives incremental merge")

    client.post("/doc/merge/discard")

    # ---------- new: chat sessions, KB routes, model swap (all stubbed) ----------
    import tempfile
    import shutil
    import importlib

    # --- chat session CRUD (temp sessions/ dir) ---
    saved_sdir = appmod.SESSIONS_DIR
    tmpd = tempfile.mkdtemp(prefix="sess_")
    appmod.SESSIONS_DIR = Path(tmpd)
    try:
        r = client.post("/api/chat/sessions", json={"name": "first"})
        check(r.status_code == 201, "create session 201")
        sid = r.get_json()["id"]
        r = client.get("/api/chat/sessions")
        check(r.status_code == 200 and len(r.get_json()["sessions"]) == 1,
              "session created -> listed")
        r = client.patch(f"/api/chat/sessions/{sid}", json={"name": "renamed"})
        check(r.status_code == 200 and r.get_json()["name"] == "renamed",
              "rename session")
        r = client.patch(f"/api/chat/sessions/{sid}", json={"kb_id": "kb1"})
        check(r.get_json()["kb_id"] == "kb1", "session kb_id set")
        r = client.patch(f"/api/chat/sessions/{sid}", json={"kb_id": None})
        check(r.get_json()["kb_id"] is None, "session kb_id cleared with null")
        r = client.get(f"/api/chat/sessions/{sid}")
        check(r.status_code == 200 and r.get_json()["messages"] == []
              and r.get_json()["name"] == "renamed", "get session")
        check(client.post("/api/chat/sessions", json={}).status_code == 400,
              "create session no name -> 400")
        check(client.post("/api/chat/sessions", json={"name": "  "}).status_code == 400,
              "create session blank name -> 400")
        check(client.get("/api/chat/sessions/nope").status_code == 404,
              "get missing session -> 404")
        check(client.patch("/api/chat/sessions/bad'id", json={"name": "x"}).status_code == 404,
              "patch malformed sid -> 404")
        check(client.patch(f"/api/chat/sessions/{sid}", json={"name": ""}).status_code == 400,
              "rename to empty -> 400")
        check(client.delete(f"/api/chat/sessions/{sid}").status_code == 200,
              "delete session 200")
        check(client.get(f"/api/chat/sessions/{sid}").status_code == 404,
              "deleted session -> 404")
    finally:
        appmod.SESSIONS_DIR = saved_sdir
        shutil.rmtree(tmpd, ignore_errors=True)

    # --- chat message route (answer_turn stubbed) ---
    saved_ans = appmod.orchestrator.answer_turn
    saved_list_kbs = appmod.rag.list_kbs

    def fake_answer_turn(user_turn, history, kb_id, max_tokens):
        return f"echo:{user_turn}:{kb_id}", [
            {"kind": "retrieval", "chunks": ["c1", "c2", "c3"]},
            {"kind": "message", "content": user_turn},
            {"kind": "answer", "content": f"echo:{user_turn}:{kb_id}"},
        ]

    appmod.orchestrator.answer_turn = fake_answer_turn
    # _kb_label resolves the assistant-message tag via rag.list_kbs; stub it so
    # the checks are offline/deterministic (id "kbab" -> "KB Ab")
    appmod.rag.list_kbs = lambda: [{"id": "kbab", "name": "KB Ab"}]
    try:
        s = client.post("/api/chat/sessions", json={"name": "chat1"}).get_json()
        sid = s["id"]
        r = client.post(f"/api/chat/sessions/{sid}/messages", json={"content": "hello"})
        check(r.status_code == 200, "chat message 200")
        j = r.get_json()
        check(j["answer"] == "echo:hello:None",
              "answer echoed, kb_id=None when unset")
        check("trace" not in j, "no trace key without developer flag")
        s2 = client.get(f"/api/chat/sessions/{sid}").get_json()
        check([m["role"] for m in s2["messages"]] == ["user", "assistant"],
              "history persisted after turn")
        check(s2["messages"][0]["content"] == "hello"
              and s2["messages"][1]["content"] == j["answer"],
              "message contents persisted")
        r = client.post(f"/api/chat/sessions/{sid}/messages",
                        json={"content": "again", "developer": True})
        j = r.get_json()
        kinds = [t["kind"] for t in j["trace"]]
        check(kinds == ["retrieval", "message", "answer"],
              f"developer=true returns trace kinds {kinds}")
        # kb selection threads through to answer_turn
        client.patch(f"/api/chat/sessions/{sid}", json={"kb_id": "kbab"})
        r = client.post(f"/api/chat/sessions/{sid}/messages", json={"content": "withkb"})
        check(r.get_json()["answer"] == "echo:withkb:kbab",
              "session kb_id threaded through answer_turn")
        r = client.post(f"/api/chat/sessions/{sid}/messages", json={"content": "h3"})
        check(len(r.get_json()["session"]["messages"]) == 8,
              "eight messages persisted across four turns")
        # (a) kb_id in the message body overrides the stored session value
        # and persists — the send carries the dropdown, so no change/send race
        r = client.post(f"/api/chat/sessions/{sid}/messages",
                        json={"content": "kboverride", "kb_id": "kbother"})
        j = r.get_json()
        check(j["answer"] == "echo:kboverride:kbother",
              "message-body kb_id overrides the session value for the turn")
        check(j["session"]["kb_id"] == "kbother",
              "message-body kb_id persisted on the session")
        # (b) assistant messages carry the KB tag: kb_id + kb_name (raw id
        # fallback for an unknown id, display name for a known one)
        last = j["session"]["messages"][-1]
        check(last["role"] == "assistant" and last["kb_id"] == "kbother"
              and last["kb_name"] == "kbother",
              "assistant message tagged with kb_id/kb_name (unknown id -> raw id)")
        j = client.post(f"/api/chat/sessions/{sid}/messages",
                        json={"content": "named", "kb_id": "kbab"}).get_json()
        last = j["session"]["messages"][-1]
        check(last["kb_id"] == "kbab" and last["kb_name"] == "KB Ab",
              "assistant kb_name resolves to the display name")
        j = client.post(f"/api/chat/sessions/{sid}/messages",
                        json={"content": "nokb", "kb_id": None}).get_json()
        last = j["session"]["messages"][-1]
        check(last.get("kb_id") is None and last.get("kb_name") is None
              and j["session"]["kb_id"] is None,
              "null kb_id -> assistant tag null + session cleared")
        check(client.post(f"/api/chat/sessions/{sid}/messages", json={}).status_code == 400,
              "empty content -> 400")
        check(client.post("/api/chat/sessions/nope/messages",
                          json={"content": "x"}).status_code == 404,
              "message to missing session -> 404")
    finally:
        appmod.orchestrator.answer_turn = saved_ans
        appmod.rag.list_kbs = saved_list_kbs

    # --- KB routes (rag_uploader._api stubbed) ---
    api_calls = []

    def stub_name(data):
        # test stub: route always sends {name}; a malformed body still must
        # not crash the fake
        try:
            return json.loads(data or b"{}")["name"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return ""

    def fake_api(method, path, data=None, headers=None):
        api_calls.append((method, path))
        if method == "GET" and path == "/api/rag/knowledge-bases":
            return {"knowledgeBases": [
                {"id": "k1", "name": "Verified OCR", "description": "d", "documentCount": 3},
                {"id": "k2", "name": "OpenSees", "description": "e", "documentCount": 9}]}
        if method == "POST" and path == "/api/rag/knowledge-bases":
            return {"id": "knew", "name": stub_name(data), "documentCount": 0}
        if method == "PATCH":
            return {"id": path.rsplit("/", 1)[-1], "name": stub_name(data)}
        if method == "DELETE":
            return {"ok": True}
        if path.endswith("/documents"):  # multipart POST data is bytes
            return {"id": "doc1", "status": "queued", "documentId": "abc"}
        return {}

    ragmod = appmod.rag
    saved_api = ragmod._api
    ragmod._api = fake_api
    # KB upload tests must not depend on this machine's real verified/ content:
    # a temp dir holding only the smoke doc's verified items.
    verified_tmp = Path(tempfile.mkdtemp(prefix="verified_"))
    (verified_tmp / "smoke").mkdir()
    for f in (REPO / "validation" / "verified" / "smoke").glob("*.json"):
        (verified_tmp / "smoke" / f.name).write_bytes(f.read_bytes())
    saved_vdir = ragmod.VERIFIED_DIR
    ragmod.VERIFIED_DIR = verified_tmp
    try:
        r = client.get("/api/kb")
        check(r.status_code == 200, "KB list 200")
        kbs = r.get_json()["knowledgeBases"]
        check([k["name"] for k in kbs] == ["Verified OCR", "OpenSees"], "KB list names")
        check(kbs[0]["documentCount"] == 3, "KB list documentCount")
        r = client.post("/api/kb", json={"name": "New KB"})
        check(r.status_code == 201 and r.get_json()["id"] == "knew", "KB create 201")
        check(("POST", "/api/rag/knowledge-bases") in api_calls, "KB create -> POST knowledge-bases")
        r = client.patch("/api/kb/k1", json={"name": "Renamed"})
        check(r.status_code == 200 and r.get_json()["name"] == "Renamed", "KB rename 200")
        check(("PATCH", "/api/rag/knowledge-bases/k1") in api_calls, "KB rename -> PATCH")
        r = client.delete("/api/kb/k1")
        check(r.status_code == 200 and r.get_json() == {"ok": True}, "KB delete 200")
        check(("DELETE", "/api/rag/knowledge-bases/k1") in api_calls, "KB delete -> DELETE")
        check(client.post("/api/kb", json={"name": " "}).status_code == 400,
              "KB create blank name -> 400")
        check(client.patch("/api/kb/k1", json={"name": ""}).status_code == 400,
              "KB rename empty -> 400")
        check(client.post("/api/kb/bad'id/upload", json={"doc_id": "x"}).status_code == 404,
              "KB upload bad kb id -> 404")
        # upload single verified doc (smoke doc has an accepted text item now)
        api_calls.clear()
        r = client.post("/api/kb/k1/upload", json={"doc_id": "smoke"})
        check(r.status_code == 200 and r.get_json()["uploaded"] == ["smoke.md"],
              "KB single-doc upload -> uploaded [smoke.md]")
        check(any(p.endswith("/documents") for _, p in api_calls),
              "single-doc upload hits documents endpoint")
        api_calls.clear()
        r = client.post("/api/kb/k1/upload", json={"doc_id": "__all__"})
        j = r.get_json()
        check(r.status_code == 200 and j["uploaded"] == ["smoke.md"] and j["skipped"] == 0,
              "__all__ upload -> [smoke.md], skipped 0")
        r = client.post("/api/kb/k1/upload", json={"doc_id": "nope"})
        check(r.status_code == 404, "upload unknown doc -> 404")
    finally:
        ragmod._api = saved_api
        ragmod.VERIFIED_DIR = saved_vdir
        shutil.rmtree(verified_tmp, ignore_errors=True)

    # --- model routes (models.current_model/unload/load stubbed) ---
    model_state: dict = {"loaded": None}
    model_calls = []
    model_jobsteps = []

    def _running_step():
        # the worker sets the step right before calling the stub, so the
        # in-memory snapshot is race-free (only one model job in flight)
        for jid, job in appmod.JOBS.items():
            if job.get("status") == "running":
                return job.get("step", "")
        return ""

    def fake_current():
        return model_state["loaded"]

    def fake_unload(path, force=True):
        model_calls.append(("unload", path))
        model_jobsteps.append("unload-step:" + _running_step())

    def fake_load(key, variant=None):
        model_calls.append(("load", key))
        if variant:
            model_calls.append(("load-variant", variant))
        model_jobsteps.append("load-step:" + _running_step())

    def fake_list_models():
        return [
            {"path": appmod.config.MODEL, "name": "GLM-OCR"},
            {"path": appmod.config.CHAT_MODEL, "name": "granite"},
        ]

    saved_m = (appmod.models.current_model, appmod.models.unload, appmod.models.load,
               appmod.models.list_models)
    appmod.models.current_model = fake_current
    appmod.models.unload = fake_unload
    appmod.models.load = fake_load
    appmod.models.list_models = fake_list_models

    def wait_job(jid):
        job = {}
        for _ in range(100):
            job = client.get(f"/jobs/{jid}").get_json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        return job

    try:
        r = client.get("/api/model")
        j = r.get_json()
        check(r.status_code == 200 and j["loaded"] is None
              and "key" not in j and j["available"] == [
                  {"path": appmod.config.MODEL, "name": "GLM-OCR"},
                  {"path": appmod.config.CHAT_MODEL, "name": "granite"}],
              "GET /api/model with nothing loaded -> available list, no key")
        # in-flight model job is exposed (header re-attaches progress on tab
        # switch / reload — tabs are full page loads, the JS toast alone dies)
        appmod.MODEL_JOB_ID = "fakej"
        appmod.JOBS["fakej"] = {"status": "running", "step": "loading granite"}
        r = client.get("/api/model")
        check(r.get_json().get("job") == {"id": "fakej", "status": "running",
                                           "step": "loading granite"},
              "/api/model exposes an in-flight model job")
        appmod.JOBS["fakej"]["status"] = "done"
        check(client.get("/api/model").get_json().get("job") is None,
              "/api/model job cleared once the job finishes")
        del appmod.JOBS["fakej"]
        appmod.MODEL_JOB_ID = None
        check(client.get("/api/model").get_json().get("job") is None,
              "/api/model job null when idle")
        model_state["loaded"] = "ggml-org/GLM-OCR-GGUF"
        r = client.get("/api/model")
        check(r.get_json()["loaded"] == "ggml-org/GLM-OCR-GGUF"
              and r.get_json()["available"],
              "/api/model reports the raw loaded path + available list")
        # (a) already loaded -> short-circuit, no job, no unload/load calls
        model_calls.clear()
        r = client.post("/api/model/load", json={"model": appmod.config.MODEL})
        check(r.status_code == 200 and r.get_json()["status"] == "done"
              and r.get_json()["step"] == "already loaded", "already-loaded short-circuits")
        check(model_calls == [], "already-loaded issues no unload/load calls")
        check(client.post("/api/model/load", json={"model": ""}).status_code == 400
              and client.post("/api/model/load", json={}).status_code == 400,
              "missing/empty model path -> 400")
        # (b) cold: unload ALWAYS precedes load, steps unloading->loading->done
        model_state["loaded"] = None
        model_calls.clear()
        model_jobsteps.clear()
        r = client.post("/api/model/load", json={"model": appmod.config.CHAT_MODEL})
        jid = r.get_json()["job_id"]
        job = wait_job(jid)
        check(job["status"] == "done" and "loaded granite" in job["step"],
              "cold job ends done/loaded granite")
        check(model_calls == [("unload", None), ("load", appmod.config.CHAT_MODEL)],
              f"cold swap calls unload() before load() ({model_calls})")
        check(any(s.startswith("unload-step:unloading") for s in model_jobsteps),
              "unload() saw job in unloading step")
        check(any(s.startswith("load-step:loading") for s in model_jobsteps),
              "load() saw job in loading step")
        # warm (GLM loaded -> chat): unload gets the GLM path first
        model_state["loaded"] = "ggml-org/GLM-OCR-GGUF"
        model_calls.clear()
        r = client.post("/api/model/load", json={"model": appmod.config.CHAT_MODEL})
        jid = r.get_json()["job_id"]
        job = wait_job(jid)
        check(job["status"] == "done", "warm swap ends done")
        check(model_calls == [("unload", "ggml-org/GLM-OCR-GGUF"),
                              ("load", appmod.config.CHAT_MODEL)],
              "warm swap unloads GLM-OCR path before loading chat")
        # per-quant load: the dropdown carries a variant, the worker passes it
        # through to models.load (subsetting the chat config pin)
        model_state["loaded"] = None
        model_calls.clear()
        r = client.post("/api/model/load",
                        json={"model": appmod.config.CHAT_MODEL, "variant": "Q4_K_M"})
        jid = r.get_json()["job_id"]
        job = wait_job(jid)
        check(job["status"] == "done"
              and ("load-variant", "Q4_K_M") in model_calls,
              "per-quant variant threaded through to models.load")
        # already-loaded matches the variant too: the same repo loaded with a
        # different quant must NOT short-circuit
        model_calls.clear()
        model_state["loaded"] = "unsloth/granite-4.1-8b-GGUF/snapshots/s1/granite-4.1-8b-UD-Q6_K_XL.gguf"
        r = client.post("/api/model/load",
                        json={"model": appmod.config.CHAT_MODEL, "variant": "UD-Q6_K_XL"})
        check(r.get_json()["status"] == "done"
              and r.get_json()["step"] == "already loaded",
              "already-loaded matches the loaded variant")
        check(model_calls == [], "same-variant load is a no-op")
        model_state["loaded"] = None
        # (c) load error -> error status propagated
        model_state["loaded"] = None
        model_calls.clear()

        def boom_load(key, variant=None):
            model_calls.append(("load", key))
            raise RuntimeError("boom")

        appmod.models.load = boom_load
        r = client.post("/api/model/load", json={"model": appmod.config.MODEL})
        jid = r.get_json()["job_id"]
        job = wait_job(jid)
        check(job["status"] == "error" and "boom" in job["error"],
              "load error -> job error status propagated")
        check(model_calls == [("unload", None), ("load", appmod.config.MODEL)],
              "error path still unloads before loading")
    finally:
        (appmod.models.current_model, appmod.models.unload, appmod.models.load,
         appmod.models.list_models) = saved_m

    # --- shared header: tabs + model control on both pages ---
    r = client.get("/")
    h = r.get_data(as_text=True)
    check('<a href="/" class="active">OCR Validation</a>' in h
          and 'id="modelSel"' in h and 'id="loadBtn"' in h,
          "/ renders active OCR tab + model dropdown")
    r = client.get("/chat")
    h = r.get_data(as_text=True)
    check('<a href="/chat" class="active">Chat</a>' in h
          and 'id="modelSel"' in h and 'id="loadBtn"' in h,
          "/chat renders active Chat tab + model dropdown")
    check('https://cdn.jsdelivr.net/npm/marked@15/marked.min.js' in h,
          "/chat includes the marked CDN for markdown rendering")

    # discard document: pending record + verified/rejected item copies removed
    r = client.post(f"/doc/{doc_id}/discard")
    check(r.status_code == 200 and not (REPO / "validation" / "pending" / f"{doc_id}.json").exists(),
          "discard removes pending record")
    check(not any((REPO / "validation" / "verified").glob(f"{doc_id}-*.json")),
          "discard cleans verified items")

    print("\nsmoke: all checks passed")


if __name__ == "__main__":
    main()