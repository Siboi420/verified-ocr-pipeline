"""Runnable assert-based check for itemizer. Run: python3 test_itemizer.py"""
import itemizer  # pyright: ignore[reportMissingImports] — same-dir module
from pathlib import Path

MD = """# Sample doc
--- Page 1 ---
Intro paragraph with inline math \\(M_u\\) here.

Some plain text, nothing special.

The capacity is given by:
$$
M_n = A_s f_y (d - a/2)
$$

--- Page 2 ---
| Column A | Column B |
|----------|----------|
| 1.5 kN   | 2.0 kN   |
| 3.0 kN   | 4.5 kN   |

Trailing paragraph with $\\rho$ inline.
"""


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def main():
    pages = itemizer.parse_document(MD, doc_id="doc1")
    check(len(pages) == 2, "two pages")
    check(pages[0]["page"] == 1 and pages[1]["page"] == 2, "page numbers 1, 2")

    p1 = pages[0]["items"]
    types1 = [(it["type"], it["has_inline_math"]) for it in p1]
    check(types1.count(("equation", False)) == 1, "page 1 has one equation")
    check(("table", False) not in types1, "page 1 has no table")
    check(types1.count(("text", True)) == 1, "page 1 has one inline-math text")
    check(types1.count(("text", False)) == 3, "page 1 has three plain texts (title, intro para, 'capacity' lead-in)")
    check(any(it["content"].startswith("# Sample doc") for it in p1), "leading title stays on page 1")
    check(p1[0]["type"] == "equation", "equation comes first on page 1")
    check(p1[0]["id"] == "doc1-p1-i1", "item id format doc1-p1-i1")
    check(p1[1]["id"] == "doc1-p1-i2" and p1[2]["id"] == "doc1-p1-i3", "sequential ids")

    p2 = pages[1]["items"]
    check(p2[0]["type"] == "table", "page 2 table first")
    check("| Column A |" in p2[0]["content"], "table content preserved")
    check(p2[0]["id"] == "doc1-p2-i1", "page 2 item id")
    check(any(it["has_inline_math"] for it in p2 if it["type"] == "text"),
          "page 2 trailing text flagged inline math")

    order = [(pg["page"], itemizer.item_priority(it["type"], it["has_inline_math"]))
             for pg in pages for it in pg["items"]]
    check(order == sorted(order), "(page, priority) ordering holds")

    # No separators -> whole doc is page 1.
    single = itemizer.parse_document("a\n\nb", "d")
    check(len(single) == 1 and single[0]["page"] == 1 and len(single[0]["items"]) == 2,
          "no separator -> single page 1")

    # Empty input -> no pages.
    check(itemizer.parse_document("", "d") == [], "empty markdown -> no pages")
    check(itemizer.parse_document("--- Page 1 ---", "d") == [], "empty page -> dropped")

    # Fixture: real OCR output of testOCR7page.pdf (Tables 21.2.1/21.2.2/21.2.3).
    fixture = Path(__file__).parent / "test-ocr-files" / "testOCR7page.md"
    if fixture.exists():
        fpages = itemizer.parse_document(fixture.read_text(), "test7")
        all_items = [it for pg in fpages for it in pg["items"]]
        check(all("chapter" in it and "section" in it for it in all_items),
              "every item carries chapter and section keys")
        for page, num in [(1, "21.2.1"), (3, "21.2.2"), (4, "21.2.3")]:
            pg = next(p for p in fpages if p["page"] == page)
            tbl = next((it for it in pg["items"]
                        if it["type"] == "table" and it.get("table_number") == num), None)
            check(tbl is not None, f"page {page} table {num} auto-paired")
            if tbl is not None:
                check(tbl["caption"].strip().startswith(f"Table {num}—"),
                      f"page {page} caption non-empty")
            check(not any(it["type"] == "text" and it["content"].strip().startswith("Table ")
                          for it in pg["items"]),
                  f"page {page}: no standalone caption text item")
        # chapter/section per fixture reality: page 1 table sits under
        # 21.2.1 within Chapter 21; page 4 equation under 21.2.3; page 7
        # opens "CHAPTER 22"; page 2 has no chapter heading (reset per page) —
        # and page 3 has no heading line at all before its table.
        p1 = next(p for p in fpages if p["page"] == 1)
        p2 = next(p for p in fpages if p["page"] == 2)
        p3 = next(p for p in fpages if p["page"] == 3)
        p4 = next(p for p in fpages if p["page"] == 4)
        p7 = next(p for p in fpages if p["page"] == 7)
        t1 = next(it for it in p1["items"] if it.get("table_number") == "21.2.1")
        check(t1["section"] == "21.2.1" and t1["chapter"] == "21",
              "page 1 table section=21.2.1, chapter=21")
        t3 = next(it for it in p3["items"] if it.get("table_number") == "21.2.2")
        check(t3["section"] is None and t3["chapter"] is None,
              "page 3 table section=None (no heading line on the page — reset per page)")
        eq4 = next(it for it in p4["items"] if it["type"] == "equation" and "\\ell_{tr}" in it["content"])
        check(eq4["section"] == "21.2.3", "page 4 equation section=21.2.3")
        check(all(it["chapter"] == "22" for it in p7["items"]),
              "page 7 chapter=22 (CHAPTER 22 heading)")
        check(all(it["chapter"] is None for it in p2["items"]),
              "page 2 has no chapter (chapter resets per page)")
    else:
        print("  skip: test-ocr-files/testOCR7page.md fixture missing")

    # Edge: caption followed by prose stays a text item.
    md_prose = """--- Page 1 ---
Table 99.1—Not a table, prose follows

Some explanatory text.

| A | B |
|---|---|
| 1 | 2 |
"""
    pp = itemizer.parse_document(md_prose, "d")[0]["items"]
    t = next(it for it in pp if it["type"] == "table")
    check("caption" not in t, "caption followed by prose stays a text item")
    check(any(it["type"] == "text" and "Table 99.1" in it["content"] for it in pp),
          "caption line remains a text item")

    # --- chapter/section metadata (RAG) ---

    # Chapter: "CHAPTER 21—…" sets chapter; following text inherits it.
    md_ch = """--- Page 1 ---
CHAPTER 21—STRENGTH REDUCTION FACTORS CODE

Some provision text.
"""
    p = itemizer.parse_document(md_ch, "d")[0]["items"]
    cap_it = next(it for it in p if "CHAPTER 21" in it["content"])
    check(cap_it["chapter"] == "21" and cap_it["section"] is None,
          "CHAPTER 21 heading sets chapter=21, section=None")
    check(next(it for it in p if it["content"] == "Some provision text.")["chapter"] == "21",
          "text after a chapter heading inherits chapter")

    # "Chapter 11" (lowercase, word prefix) is captured too.
    p = itemizer.parse_document("--- Page 1 ---\nChapter 11—Stuff\n\nbody.\n", "d")[0]["items"]
    check(p[0]["chapter"] == "11", "Chapter 11 (lowercase, word prefix) -> chapter 11")

    # Chapter state resets per page.
    pgs = itemizer.parse_document("--- Page 1 ---\nCHAPTER 21—x\n\ntext.\n\n--- Page 2 ---\nMore text.\n", "d")
    check(pgs[0]["items"][0]["chapter"] == "21" and pgs[1]["items"][0]["chapter"] is None,
          "chapter resets across pages")

    # Section: heading carries its own number; prose/table/equation inherit;
    # R-prefixed headings are distinct from plain ones.
    md_sec = """--- Page 1 ---
CHAPTER 21—x

21.2.1 Strength reduction factors.

Prose following the heading.

Table 21.2.1—Cap

| A | B |
|---|---|
| 1 | 2 |

The equation:

$$
M = f(A)
$$

R21.2.1 Commentary.
"""
    p = itemizer.parse_document(md_sec, "d")[0]["items"]
    h = next(it for it in p if it["content"].startswith("21.2.1 "))
    check(h["section"] == "21.2.1" and h["chapter"] == "21",
          "heading item carries its own section+chapter")
    check(next(it for it in p if it["content"] == "Prose following the heading.")["section"] == "21.2.1",
          "prose after a heading inherits its section")
    tbl = next(it for it in p if it["type"] == "table")
    check(tbl["section"] == "21.2.1", "table inherits the preceding section")
    eq = next(it for it in p if it["type"] == "equation")
    check(eq["section"] == "21.2.1", "equation inherits the preceding section")
    r = next(it for it in p if it["content"].startswith("R21.2.1 "))
    check(r["section"] == "R21.2.1" and r["section"] != "21.2.1",
          "R-prefixed heading is its own section")

    # Section state resets per page; None before the first heading.
    pgs = itemizer.parse_document("--- Page 1 ---\n21.3.1 Heading.\n\n--- Page 2 ---\nText without heading.\n", "d")
    check(pgs[1]["items"][0]["section"] is None, "section resets across pages")
    p = itemizer.parse_document("--- Page 1 ---\nSome early text.\n\n21.1—Scope\n", "d")[0]["items"]
    early = next(it for it in p if it["content"] == "Some early text.")
    check(early["section"] is None and early["chapter"] is None,
          "section/chapter are None before the first heading on a page")

    # Decimal prose does not set the section ("0.75" fails the [1-9] guard).
    md_dec = """--- Page 1 ---

21.2.1 Design factors.

0.75 is used for shear and 0.90 for moment.
"""
    p = itemizer.parse_document(md_dec, "d")[0]["items"]
    check(next(it for it in p if it["content"].startswith("0.75"))["section"] == "21.2.1",
          "decimal prose ('0.75 is used…') does not reset the section")

    # Inline cross-references are citations, not headings.
    md_ref = """--- Page 1 ---
21.2.2 Moment strength.

The value is in accordance with 21.2.1.
"""
    p = itemizer.parse_document(md_ref, "d")[0]["items"]
    check(next(it for it in p if it["content"].startswith("The value"))["section"] == "21.2.2",
          "inline section reference does not change the section")

    # --- clean_export_text: boilerplate dropped at export time only ---
    ce = itemizer.clean_export_text
    check(ce("# OCR: testOCR7page.pdf") == "", "# OCR: title line dropped")
    check(ce("CODE\nCOMMENTARY\n## CODE\n**Commentary**\n**CODE**") == "",
          "standalone CODE/COMMENTARY markers dropped (any # / ** wrapping)")
    check(ce("CHAPTER 21—STRENGTH REDUCTION FACTORS CODE") == "CHAPTER 21—STRENGTH REDUCTION FACTORS CODE",
          "'CHAPTER 21—… CODE' prose kept")
    check(ce("# OCR: a.pdf\n\nCHAPTER 21—Scope CODE\n\n|A|\n") == "\nCHAPTER 21—Scope CODE\n\n|A|",
          "OCR title dropped; chapter line and table kept")
    check(ce("| A | B |\n|---|---|\n| 1 | 2 |") == "| A | B |\n|---|---|\n| 1 | 2 |",
          "table pipe content unchanged")
    check(ce("Design values CODE and COMMENTARY") == "Design values CODE and COMMENTARY",
          "CODE/COMMENTARY inside prose kept")
    check(ce("# OCR: x.pdf\n\n**Commentary**\n\nReal text.") == "\n\nReal text.",
          "OCR title + bold commentary dropped, real text kept")

    # parse_table_caption: empty -> (None, None); caption+number; fallback line.
    check(itemizer.parse_table_caption("") == (None, None),
          "parse_table_caption empty -> (None, None)")
    check(itemizer.parse_table_caption("Table 21.2.1—Strength reduction factors")
          == ("Table 21.2.1—Strength reduction factors", "21.2.1"),
          "parse_table_caption caption with number")
    check(itemizer.parse_table_caption("Member capacities") == ("Member capacities", None),
          "parse_table_caption fallback: first non-empty line, no number")
    html_cap = '<table border="1"><tr><td>Table 21.2.1—Strength reduction factors $\\phi$</td></tr></table>'
    check(itemizer.unwrap_html_caption(html_cap)
          == "Table 21.2.1—Strength reduction factors $\\phi$",
          "unwrap_html_caption: single-cell HTML table -> cell text")
    check(itemizer.unwrap_html_caption("plain text") == "plain text",
          "unwrap_html_caption: non-HTML text unchanged")

    # pick_caption_from_band: strict — only a real "Table N…" line wins.
    check(itemizer.pick_caption_from_band("Table 21.2.1—Strength reduction factors $\\phi$")
          == ("Table 21.2.1—Strength reduction factors $\\phi$", "21.2.1"),
          "pick_caption_from_band: caption line")
    check(itemizer.pick_caption_from_band(
        '<table border="1"><tr><td>Table 12.3—Foo</td></tr></table>')
          == ("Table 12.3—Foo", "12.3"),
          "pick_caption_from_band: HTML-wrapped caption")
    check(itemizer.pick_caption_from_band("Action or structural element | φ | Exceptions")
          == (None, None), "pick_caption_from_band: table header -> no caption")
    check(itemizer.pick_caption_from_band("") == (None, None),
          "pick_caption_from_band: empty -> none")

    # GLM sometimes emits an unclosed <table>…</tbody> (no </table>).
    unclosed = "--- Page 1 ---\n\n<p>lead</p>\n<table><thead><tr><th>A</th><th>B</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody>\n\nText after.\n"
    up = itemizer.parse_document(unclosed, "d")[0]["items"]
    check(any(it["type"] == "table" and "|A|" in it["content"] for it in up),
          "unclosed <table>…</tbody> converts to a table item")
    check(any(it["type"] == "text" and it["content"] == "Text after." for it in up),
          "text after the unclosed table is not eaten")

    print("itemizer: all checks passed")


if __name__ == "__main__":
    main()