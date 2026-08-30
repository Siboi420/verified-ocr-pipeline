"""Parse OCR markdown into page-scoped review items: equations, tables, text.

Document structure expected from ocr_engine.assemble_markdown:
    --- Page 1 ---
    ...page content...
    --- Page 2 ---
    ...
If no page separators are present, the whole file counts as page 1.

Every item also carries "chapter" and "section" (RAG metadata): the nearest
preceding "CHAPTER N" heading number and nearest preceding dotted heading
(e.g. "21.2.1", "R21.2.1"), both resetting per page. Line-start anchoring is
deliberate: inline cross-references ("...in accordance with 21.2.1...") are
citations, not ownership, and never set the section.
"""
import re

PAGE_RE = re.compile(r"^--- Page (\d+) ---\s*$")
EQUATION_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
INLINE_MATH_RE = re.compile(r"\\\(.+?\\\)|\$[^$\n]+\$")
# A caption line: "Table 21.2.1—..." (em dash/dash/space after the number).
TABLE_CAPTION_RE = re.compile(r"^\s*Table\s+(\d+(?:\.\d+)*)\b(.*)$", re.IGNORECASE)

# Equation reference markers: leading "(a)" and trailing "(22.5.1.10a)".
# GLM-OCR emits them OUTSIDE the $...$ span: "(a) $$x$$ (22.5.1.10a)".
EQ_LETTER_HEAD_RE = re.compile(r"^\s*\(([a-z])\)")          # "(a) ..."
EQ_NUM_TAIL_RE = re.compile(r"\((\d+(?:\.\d+)*(?:[a-z])?)\)\s*$")  # "... (22.5.1.10a)"

# Chapter heading: "CHAPTER 21—..." / "Chapter 11" (word prefix, any digit count).
CHAPTER_RE = re.compile(r"^\*{0,2}\s*CHAPTER\s+(\d+)\b", re.IGNORECASE)
# Section heading: "21.2", "21.2.1.1.1", "R21.2.1" — 2-5 dotted components.
# [1-9] first digit guards against decimal prose like "0.65 to 0.90"; a
# whitespace/dash must follow the number ("The value in accordance with
# 21.2.1." starts with 'The', so it never matches anyway).
# GLM-OCR bolds provision/heading numbers (">**22.5.1.2** Cross-section…").
# Tolerate an optional leading/raw ** marker; [1-9] first digit guards against
# decimal prose like "0.65 to 0.90"; a whitespace/dash/marker must follow the
# number ("The value in accordance with 21.2.1." starts with 'The').
SECTION_RE = re.compile(r"^\*{0,2}((?:R)?[1-9]\d*\.\d+(?:\.\d+){0,3})(?=\s|—|–|-|\*\*)")
HTML_TABLE_RE = re.compile(
    r"<table\b.*?</table\s*>"  # well-formed
    r"|<table\b.*?</tbody\s*>"  # GLM often omits </table>
    r"|<table\b[^>]*>.*?(?=\n\s*\n|\Z)",  # unclosed, run to blank line/EOF
    re.IGNORECASE | re.DOTALL
)

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

def parse_table_caption(text):
    """Parse caption-box OCR text into (caption_text, table_number).

    A line starting "Table N.N.N …" wins (the full line is the caption, the
    number is extracted). Otherwise the first non-empty line becomes the
    caption with table_number=None (manual caption-box path). Whitespace-only
    text returns (None, None).
    """
    if not text or not text.strip():
        return None, None
    lines = [ln.strip() for ln in text.strip().splitlines()]
    for ln in lines:
        m = TABLE_CAPTION_RE.match(ln)
        if m:
            return ln, m.group(1)
    return lines[0], None


def unwrap_html_caption(text):
    """If text is an HTML table (GLM wraps tiny crops), strip tags and keep
    the text lines. The caption box is drawn around one line, so the first
    remaining text line is the caption."""
    if not text.lstrip().lower().startswith("<table"):
        return text
    plain = re.sub(r"<[^>]+>", "", text)
    return "\n".join(ln.strip() for ln in plain.splitlines() if ln.strip())


def pick_caption_from_band(text):
    """Extract a table caption from OCR of a box's top band.

    Unlike parse_table_caption there is NO first-line fallback: only a line
    that actually starts "Table N…" is trusted, otherwise (None, None) —
    otherwise a band landing on a table header would become a bogus caption.
    """
    plain = unwrap_html_caption(text)
    for ln in plain.splitlines():
        if ln.strip():
            m = TABLE_CAPTION_RE.match(ln.strip())
            return (ln.strip(), m.group(1)) if m else (None, None)
    return None, None


def _is_table(rows):
    return len(rows) >= 2 and any(TABLE_SEP_RE.match(r) for r in rows)


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


def _scan_gap(text, base_line):
    """Run the pipe-table + caption logic over a body gap; return ordered events.

    Mirrors the old _extract_tables/_extract_text pass for a gap: caption lines
    ("Table N…") are held across blank lines and attach to the next valid pipe
    table; a pending caption followed by prose is flushed back to text. Each
    event carries the body line (1-based) of its start so callers can replay
    document order — a paragraph whose first line precedes a table sorts before
    it even though the paragraph only materializes after the table closes.
    """
    events = []
    kept_l = []  # text lines in order (blanks included; they separate paragraphs)
    kept_n = []  # body line numbers parallel to kept_l
    block = []  # pipe rows of the current block
    block_n = base_line
    pending = None  # (caption_text, table_number) awaiting a table below
    pending_n = base_line
    pending_blanks = 0  # blank lines after the caption, emitted only on flush
    lines = text.splitlines()

    def flush_pending():
        nonlocal pending, pending_blanks
        if pending is not None:
            kept_l.append(pending[0])
            kept_n.append(pending_n)
            if pending_blanks:
                for _ in range(pending_blanks):
                    kept_l.append("")
                    kept_n.append(pending_n)  # only the boundary matters
            pending = None
            pending_blanks = 0

    def attach_pending():
        nonlocal pending, pending_blanks
        pair = (pending[1], pending[0]) if pending is not None else (None, None)
        pending = None
        pending_blanks = 0
        return pair

    for i, line in enumerate(lines):
        ln = base_line + i
        if TABLE_ROW_RE.match(line):
            if not block:
                block_n = ln
            block.append(line)
            continue
        if block:  # non-pipe line closes the current block
            if _is_table(block):
                num, cap = attach_pending()
                events.append({"line": block_n, "kind": "table",
                               "content": "\n".join(block),
                               "caption": cap, "table_number": num})
            else:
                flush_pending()
                kept_l.extend(block)
                kept_n.extend([block_n] * len(block))
            block = []
        m = TABLE_CAPTION_RE.match(line)
        if m:
            if pending is not None:
                flush_pending()  # previous caption wasn't for this table
            pending = (line, m.group(1))
            pending_n = ln
            pending_blanks = 0
            continue
        if not line.strip():
            if pending is not None:
                pending_blanks += 1  # blank line: keep the caption alive
            else:
                kept_l.append(line)
                kept_n.append(ln)
            continue
        if pending is not None:
            flush_pending()  # caption followed by prose: back to text
        kept_l.append(line)
        kept_n.append(ln)
    if block:
        if _is_table(block):
            num, cap = attach_pending()
            events.append({"line": block_n, "kind": "table",
                           "content": "\n".join(block),
                           "caption": cap, "table_number": num})
        else:
            flush_pending()
            kept_l.extend(block)
            kept_n.extend([block_n] * len(block))
    else:
        flush_pending()  # trailing caption with no table below

    # Split kept lines into paragraphs (blank runs separate them), equivalent to
    # re.split(r"\n\s*\n") on the joined text. The paragraph event's line is
    # its FIRST non-blank line, so a paragraph spanning a table still sorts
    # before the table for section-tracking purposes.
    n = len(kept_l)
    i = 0
    while i < n:
        if not kept_l[i].strip():
            i += 1
            continue
        start = i
        while i < n and kept_l[i].strip():
            i += 1
        events.append({"line": kept_n[start], "kind": "text",
                       "content": "\n".join(kept_l[start:i]).strip()})
    return events


def eq_refs(front, back):
    """(letters, num) of an equation from its line text.

    front = text before/inside the span start; back = span content + trailing
    text. Returns (None, None) when neither marker is present.
    """
    m = EQ_LETTER_HEAD_RE.search(front)
    letters = m.group(1) if m else None
    m = EQ_NUM_TAIL_RE.search(back)
    num = m.group(1) if m else None
    return letters, num


def _parse_page_body(body):
    """Scan one page's body in document order; return items with chapter/section.

    Two phases: (1) extract equation and HTML-table spans (overlapping spans
    skip — earlier/outer wins) and run the pipe-table/caption scan over the
    gaps between them, collecting ordered events with body line numbers;
    (2) replay events in document order, tracking current chapter/section
    (both reset per page) and stamping every item. Heading lines update the
    state with their own number before the standing item is stamped —
    following items inherit it. Every line of a paragraph is checked, not
    just the first: GLM-OCR merges a heading with its first provision line on
    consecutive lines ("21.2—…\n21.2.1 Strength…"), and both regexes are
    line-start anchored, so inline cross-references and decimals ("0.75") can
    never match anyway.
    """
    spans = [(m.start(), m.end(), "equation", m) for m in EQUATION_RE.finditer(body)]
    spans += [(m.start(), m.end(), "html", m) for m in HTML_TABLE_RE.finditer(body)]
    spans.sort(key=lambda s: s[0])
    accepted = []
    for s in spans:
        s0, e0, _, _ = s
        if any(s0 < e1 and e0 > s1 for s1, e1, _, _ in accepted):
            continue  # overlapping span: the earlier/outer one wins
        accepted.append(s)

    events = []  # {"line": int, "kind": ..., "content": str, ...}
    pos = 0
    for s0, e0, kind, m in accepted:
        events.extend(_scan_gap(body[pos:s0], body.count("\n", 0, pos) + 1))
        if kind == "equation":
            content = (m.group(1) or m.group(2) or "").strip()
            ls = body.rfind("\n", 0, s0) + 1
            le = body.find("\n", e0)
            if le == -1:
                le = len(body)
            letters, num = eq_refs(body[ls:s0] + content, content + body[e0:le])
            events.append({"line": body.count("\n", 0, s0) + 1, "kind": "equation",
                           "content": content, "eq_letters": letters, "eq_num": num})
        else:
            events.append({"line": body.count("\n", 0, s0) + 1, "kind": "html",
                           "content": m.group(0).strip()})
        pos = e0
    events.extend(_scan_gap(body[pos:], body.count("\n", 0, pos) + 1))

    items = []
    chapter = section = None
    for ev in sorted(events, key=lambda e: e["line"]):
        if ev["kind"] == "text":
            for ln in ev["content"].splitlines():
                m = CHAPTER_RE.match(ln)
                if m:
                    chapter = m.group(1)
                m = SECTION_RE.match(ln)
                if m:
                    section = m.group(1)
            item = {"type": "text", "content": ev["content"],
                    "has_inline_math": bool(INLINE_MATH_RE.search(ev["content"]))}
        elif ev["kind"] == "equation":
            item = {"type": "equation", "content": ev["content"], "has_inline_math": False}
            if ev.get("eq_letters") is not None:
                item["eq_letters"] = ev["eq_letters"]
            if ev.get("eq_num") is not None:
                item["eq_num"] = ev["eq_num"]
        elif ev["kind"] == "html":
            md = _html_to_markdown(ev["content"])
            item = {"type": "table" if md else "text", "content": md if md else ev["content"],
                    "has_inline_math": False}
        else:  # pipe table
            item = {"type": "table", "content": ev["content"], "has_inline_math": False}
            if ev.get("caption") is not None:
                item["caption"] = ev["caption"]
                item["table_number"] = ev["table_number"]
        item["chapter"] = chapter
        item["section"] = section
        items.append(item)
    return items


def parse_document(markdown, doc_id=""):
    """Parse OCR markdown into page-scoped review items.

    Returns [{"page": n, "items": [item, ...]}, ...] with each item:
      {"id": "<doc_id>-p<n>-i<k>", "type": "equation|table|text",
       "content": str, "has_inline_math": bool, "status": "pending",
       "chapter": str|None, "section": str|None}
    k is 1-based within the page. Items are ordered equation -> table -> text,
    which equals priority order; the UI re-sorts by (page, priority) anyway.
    """
    pages_out = []
    for page_num, body in _split_pages(markdown):
        items = _parse_page_body(body)
        # (page, priority) ordering: stable, so document order holds within a priority.
        items.sort(key=lambda it: item_priority(it["type"], it["has_inline_math"]))
        for k, it in enumerate(items, start=1):
            it["id"] = f"{doc_id}-p{page_num}-i{k}"
            it["status"] = "pending"
        if items:  # pages with no items are not worth reviewing
            pages_out.append({"page": page_num, "items": items})
    return pages_out


def clean_export_text(text):
    """Strip GLM-OCR boilerplate from exported content (export-time only).

    Drops lines matching "# OCR:" (the generated title) and standalone
    CODE/COMMENTARY markers (case-insensitive, allowing leading '#'s and
    wrapping '**'). Real prose — "CHAPTER 21—… CODE" — is unaffected. The
    review UI and doc JSON keep the raw content.
    """
    out = []
    for line in text.splitlines():
        if re.match(r"^#\s*OCR:", line):
            continue
        core = line.lstrip("#").strip().strip("*")
        if core.lower() in ("code", "commentary"):
            continue
        out.append(line)
    return "\n".join(out)