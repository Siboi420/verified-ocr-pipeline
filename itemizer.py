"""Parse OCR markdown into page-scoped review items: equations, tables, text.

Document structure expected from ocr_engine.assemble_markdown:
    --- Page 1 ---
    ...page content...
    --- Page 2 ---
    ...
If no page separators are present, the whole file counts as page 1.
"""
import re

PAGE_RE = re.compile(r"^--- Page (\d+) ---\s*$")
EQUATION_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
INLINE_MATH_RE = re.compile(r"\\\(.+?\\\)|\$[^$\n]+\$")

HTML_TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)

TYPE_PRIORITY = {"equation": 0, "table": 1, "text-math": 2, "text": 3}


def item_priority(item_type, has_inline_math=False):
    """Priority for sorting within a page: equation < table < text-math < text."""
    if has_inline_math:
        return TYPE_PRIORITY["text-math"]
    return TYPE_PRIORITY[item_type]


def _split_pages(markdown):
    """Return [(page_num, body_text)] in document order."""
    sections = []
    buf = []
    cur_page = None  # content before the first separator has no page yet
    for line in markdown.splitlines():
        m = PAGE_RE.match(line)
        if m:
            try:
                n = int(m.group(1))
            except ValueError:
                buf.append(line)
                continue
            sections.append((cur_page, "\n".join(buf)))
            cur_page = n
            buf = []
        else:
            buf.append(line)
    sections.append((cur_page, "\n".join(buf)))

    numbered = [(p, t) for p, t in sections if p is not None]
    if not numbered:
        return [(1, markdown)]  # no separators: whole file is page 1

    # Content before the first separator belongs on the first numbered page.
    leading = "\n".join(t for p, t in sections if p is None)
    page, text = numbered[0]
    first_text = (leading + "\n\n" + text).strip()
    merged = [(page, first_text)] if first_text else []
    merged += [(p, t) for p, t in numbered[1:] if t.strip()]
    return merged


def _extract_equations(body):
    """Pull $$...$$ / \\[...\\] blocks out of the body.

    Returns (equation_spans, remaining_text) where equation_spans is a list of
    raw equation strings found, and the math content is blanked out of the text.
    """
    spans = []

    def _blank_out(m):
        """Record a regex match as an equation item, blank its text."""
        inner = m.group(1) or m.group(2) or ""
        spans.append(inner.strip())
        return "\n" * len(m.group(0).splitlines())  # keep line structure

    rest = EQUATION_RE.sub(_blank_out, body)
    return spans, rest


def _extract_tables(body):
    """Pull pipe-table blocks out of the body.

    Returns (tables, remaining_text); a table is the raw block of consecutive
    pipe rows containing a |-...-| separator row with >= 2 rows total.
    """
    tables = []
    kept = []
    block = []
    lines = body.splitlines()
    for line in lines:
        if TABLE_ROW_RE.match(line):
            block.append(line)
        else:
            if block and _is_table(block):
                tables.append("\n".join(block))
            elif block:
                kept.extend(block)
            block = []
            kept.append(line)
    if block:
        if _is_table(block):
            tables.append("\n".join(block))
        else:
            kept.extend(block)
    return tables, "\n".join(kept)


def _is_table(rows):
    return len(rows) >= 2 and any(TABLE_SEP_RE.match(r) for r in rows)


def _extract_html_tables(body):
    """Pull HTML <table>...</table> blocks out of the body (GLM-OCR sometimes
    answers tables in HTML when asked for markdown). Returns (tables, rest)."""
    tables = []
    rest = []
    pos = 0
    for m in HTML_TABLE_RE.finditer(body):
        tables.append(m.group(0).strip())
        rest.append(body[pos:m.start()])
        pos = m.end()
    rest.append(body[pos:])
    return tables, "".join(rest)


def _html_to_markdown(html):
    """Convert a minimal <table>...</table> into | pipe | markdown rows.

    Covers the tags GLM-OCR emits: table/thead/tbody/tr/th/td (+colspan).
    Every row becomes a pipe row; td/th cell text is stripped of tags and
    escaped. Returns the markdown block, or None if it cannot be parsed."""
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", html, re.IGNORECASE | re.DOTALL)
    if not rows:
        return None
    md_rows = []
    for r in rows:
        cells = re.findall(r"<(?:t[dh])\b[^>]*>(.*?)</(?:t[dh])\s*>", r, re.IGNORECASE | re.DOTALL)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        md_rows.append("|" + "|".join("" if c is None else c.replace("|", "\\|") for c in cells) + "|")
    if len(md_rows) < 2:
        return None
    ncols = max(len(r.split("|")) - 2 for r in md_rows)
    sep = "|" + "|".join(["---"] * ncols) + "|"
    return "\n".join([md_rows[0], sep] + md_rows[1:])


def _extract_text(body):
    """Split remaining text into paragraphs; each becomes a text item."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body)]
    return [p for p in paras if p]


def parse_document(markdown, doc_id=""):
    """Parse OCR markdown into page-scoped review items.

    Returns [{"page": n, "items": [item, ...]}, ...] with each item:
      {"id": "<doc_id>-p<n>-i<k>", "type": "equation|table|text",
       "content": str, "has_inline_math": bool, "status": "pending"}
    k is 1-based within the page. Items are ordered equation -> table -> text,
    which equals priority order; the UI re-sorts by (page, priority) anyway.
    """
    pages_out = []
    for page_num, body in _split_pages(markdown):
        html_tables, body = _extract_html_tables(body)
        equations, rest = _extract_equations(body)
        tables, rest = _extract_tables(rest)
        paragraphs = [p for p in _extract_text(rest)]

        items = []
        for eq in equations:
            items.append({"type": "equation", "content": eq, "has_inline_math": False})
        for t in tables:
            items.append({"type": "table", "content": t, "has_inline_math": False})
        for ht in html_tables:
            md = _html_to_markdown(ht)
            if md:
                items.append({"type": "table", "content": md, "has_inline_math": False})
            else:  # unparseable: keep as text, visibly marked
                items.append({"type": "text", "content": ht, "has_inline_math": False})
        for para in paragraphs:
            items.append({
                "type": "text",
                "content": para,
                "has_inline_math": bool(INLINE_MATH_RE.search(para)),
            })

        # (page, priority) ordering: stable, so document order holds within a priority.
        items.sort(key=lambda it: item_priority(it["type"], it["has_inline_math"]))
        for k, it in enumerate(items, start=1):
            it["id"] = f"{doc_id}-p{page_num}-i{k}"
            it["status"] = "pending"
        if items:  # pages with no items are not worth reviewing
            pages_out.append({"page": page_num, "items": items})
    return pages_out