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

    print("itemizer: all checks passed")


if __name__ == "__main__":
    main()