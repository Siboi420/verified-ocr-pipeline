#!/usr/bin/env python3
"""Push verified review items into an Unsloth Studio RAG knowledge base.

Reads validation/verified/*.json, groups items by source doc, renders one
markdown file per doc (chapter/section metadata + clean content), and uploads
to the KB. Unsloth Studio chunks + embeds server-side; this script only does
the doc assembly and the HTTP upload.

Usage:
    UNSLOTH_API_KEY=... python3 rag_uploader.py [--kb "Verified OCR"] [--dry-run]
"""
import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path


# Equation key -> the section it belongs to: the dotted prefix of the key
# ("R22.5.6.3.1a" -> "R22.5.6.3.1", "22.5.1.2" -> "22.5.1.2"). ACI keys
# encode their own section, so this backfills missing section anchors.
_EQ_SECTION_RE = re.compile(r"^(R?\d+(?:\.\d+){1,4})[a-z]?$")

# A numbered provision/heading statement that can introduce an equation
# ("**22.5.1.2** Cross-sectional dimensions shall be selected to satisfy…").
# Captures (R?, number); "**" bold tolerated. R-commentary is excluded
# downstream — it is context, not the provision itself.
_STMT_RE = re.compile(r"^\*{0,2}(R)?(\d+(?:\.\d+)+)[a-z]?\*{0,2}(?:\s|—|–|-).+")


def _eq_section(num):
    m = _EQ_SECTION_RE.match(str(num or "").strip().strip("()"))
    return m.group(1) if m else None


def _eq_parent(section):
    """The parent provision of a section ("22.5.6.3.1" -> "22.5.6.3");
    None for a top-level dotted number. Sub-lettered equations (22.5.6.3.1c)
    are introduced by their parent provision ("22.5.6.3 For…")."""
    parts = (section or "").split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def _stmt_key(content):
    """Dotted provision number a statement's marker carries
    ("**22.5.1.2** Cross-sectional…" -> "22.5.1.2"); None for non-statements
    and R-commentary."""
    c = content.strip()
    if len(c) > 400:
        return None
    m = _STMT_RE.match(c)
    if not m or m.group(1):
        return None
    return m.group(2)


def _statement_score(text):
    """Higher = more provision-like (the direct rule that introduces an
    equation, "…shall be calculated by:") vs R-commentary explanation."""
    s = text.strip()
    score = 0
    if "shall" in s:
        score += 2
    if "calculated by" in s or "satisfy" in s or "shall be" in s:
        score += 1
    if s.startswith("R"):
        score -= 3  # commentary
    return score


API_BASE = os.environ.get("UNSLOTH_API_BASE", "http://127.0.0.1:8888")
VERIFIED_DIR = Path(__file__).parent / "validation" / "verified"
DEFAULT_KB = "Verified OCR"


def _api(method, path, data=None, headers=None):
    req = urllib.request.Request(
        API_BASE + path, method=method, data=data,
        headers={"Authorization": f"Bearer {os.environ['UNSLOTH_API_KEY']}",
                 **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
        raise SystemExit(f"api {method} {path} failed: {e}") from e


def get_or_create_kb(name):
    for kb in _api("GET", "/api/rag/knowledge-bases").get("knowledgeBases", []):
        if kb["name"] == name:
            return kb["id"]
    kb = _api("POST", "/api/rag/knowledge-bases",
              data=json.dumps({"name": name,
                               "description": "Verified OCR exports from seismic-ai-tools"}).encode(),
              headers={"Content-Type": "application/json"})
    print(f"created KB {name!r} ({kb['id']})")
    return kb["id"]


def docs_from_verified(verified_dir):
    """Group verified item JSONs by doc_id, oldest-page-ordered list each.
    Corrupt/unreadable files are skipped with a warning; so are stale
    pre-metadata exports (missing source_name) — they are bbox-OCR
    experiment artifacts, not review items."""
    by_doc = {}
    for p in sorted(verified_dir.glob("*.json")):
        try:
            it = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"warning: skipping unreadable {p.name}: {e}")
            continue
        if "source_name" not in it:
            print(f"warning: skipping pre-metadata export {p.name}")
            continue
        by_doc.setdefault(it["doc_id"], []).append(it)
    return by_doc


def _item_order(it):
    """Document order within a page: the trailing numeric index of item_id
    ("…-p404-i2" -> 2). Fallback 0 keeps bbox-added items (no index) first;
    stable sort preserves their append order."""
    try:
        return int(it.get("item_id", "").rsplit("i", 1)[-1])
    except ValueError:
        return 0


def _item_id(it):
    """Stable identity for sort-meta maps (verified exports carry item_id)."""
    return it.get("item_id") or it.get("id")


# Plain-text decoding for table cells: a small ACI-domain renderer so the
# embedding sees query-matchable words instead of pipes + LaTeX (table chunks
# have no provision statement to fold, so the raw content is all math).
_SYM_TO_UNICODE = {
    "\\lambda": "λ", "\\rho": "ρ", "\\phi": "φ", "\\mu": "μ",
    "\\alpha": "α", "\\beta": "β", "\\sigma": "σ",
    "\\geq": "≥", "\\leq": "≤", "\\ne": "≠", "\\times": "×",
    "\\cdot": "·", "\\prime": "′", "\\max": "max", "\\min": "min",
}


def _math_to_text(s):
    """Decode a LaTeX-ish math cell to readable Unicode, e.g.
    "$$\\lambda_s\\lambda(\\rho_w)^{1/3}\\sqrt{f_c'}$$
    -> "λ_sλ(ρ_w)1/3√(f_c')". Subscripts keep their underscore so λ_s/ρ_w
    stay distinct segmentable tokens (never merged into unsegmentable
    lambdas/rhow, the KB prose λ-drop bug); superscripts (^) and braces are
    dropped."""
    t = s.strip()
    if not t:
        return ""
    t = t.replace("$$", "").replace("$", "")
    # keep the bracket/paren that \left/\right point at
    t = re.sub(r"\\left\s*(.)", r"\1", t)
    t = re.sub(r"\\right\s*(.)", r"\1", t)
    # drop braces around sub/superscripts, KEEPING the "_" of subscripts
    # (_{s} -> _s) and flattening "^" superscripts (^{1/3} -> 1/3); doing
    # this early lets \frac/\sqrt below see clean interiors
    t = re.sub(r"_\{([^{}]*)\}", r"_\1", t)
    t = re.sub(r"\^\{([^{}]*)\}\s*", r"\1 ", t)
    # \frac{X}{Y} -> (X)/(Y) — best-effort (nested braces in the numerator
    # like N_{u} defeat the non-brace group; leftover frac keywords are
    # dropped below and the tokens still carry the meaning)
    t = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", t)
    # \sqrt{X} -> √(X)
    t = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√(\1)", t)
    t = re.sub(r"\\frac", " ", t)  # leftover fracs drop the keyword
    # Greek + operator words -> Unicode
    for k, v in _SYM_TO_UNICODE.items():
        t = t.replace(k, v)
    # leftover braces/backslashes; strip ^ but KEEP _ (subscript stays a
    # distinct token — the KB prose λ-drop bug)
    t = t.replace("{", "").replace("}", "").replace("\\", "")
    t = t.replace("^", "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _table_prose(content):
    """A normalized markdown-table rendition of a pipe table's rows: each cell
    decoded to clean Unicode via _math_to_text. This is the table's SINGLE
    canonical representation (readable + query-matchable); the raw LaTeX pipe
    mirror is not emitted separately (dedupe)."""
    out = []
    for ln in content.splitlines():
        line = ln.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set(cells) <= {"", "-", ":", ":-", "-:", "---"}:
            continue  # separator row
        dec = [_math_to_text(c) for c in cells]
        out.append("| " + " | ".join(dec) + " |")
    return "\n".join(out)


# Symbols table chunks use that are defined elsewhere in ACI; inlined (only
# when the table actually uses them) so the model doesn't hunt for local
# meaning and hedge. Keys are the decoded-token forms _math_to_text emits.
_SYMBOL_DEFS = [
    ("A_g", "A_g = gross area of concrete cross section"),
    ("b_w", "b_w = web (member) width"),
    ("b_o", "b_o = perimeter of the critical section for two-way shear"),
    ("N_u", "N_u = factored axial force, positive for compression"),
    ("β", "β = ratio of long to short side of the column/load area"),
    ("α_s", "α_s = a constant depending on column location, per 22.6.5.3"),
]


def _inline_symbol_defs(text):
    """Definitions for table symbols the text uses, so the chunk is locally
    self-explanatory (else the model hedges on symbols defined elsewhere)."""
    return [d for tok, d in _SYMBOL_DEFS if tok in text]


def _section_key(it):
    """The section key an item belongs to, for ordering:
    stamped section -> equation key -> table number -> statement marker ->
    chapter (weak fallback). None if none apply."""
    if it.get("section"):
        return it["section"]
    if it.get("type") == "equation" and it.get("eq_num"):
        return _eq_section(it["eq_num"])
    if it.get("type") == "table" and it.get("table_number"):
        return it["table_number"]
    k = _stmt_key(it.get("content") or "")
    if k:
        return k
    if it.get("chapter"):
        return it["chapter"]
    return None


def _sort_tuple(sec):
    """(numeric_tuple, is_R) from a section key: "R22.5.6.3.1a" ->
    ((22,5,6,3,1), True). Trailing letters stripped (eq/table sub-suffixes);
    unparseable -> ((), is_R) so it lands in its R/code tail."""
    s = (sec or "").strip()
    is_r = s.startswith("R")
    s = s.lstrip("R").strip()
    s = re.sub(r"[A-Za-z]+$", "", s)
    try:
        return tuple(int(x) for x in s.split(".") if x != ""), is_r
    except ValueError:
        return (), is_r


def _page_order(items):
    """Per-page document order: the numeric index of item_id ("…-p404-i2" -> 2).
    bbox-added items (no index) keep append order (stable)."""
    return sorted(items, key=_item_order)


def render_markdown(items):
    """One markdown doc per source file. Empty content (boilerplate-only
    items are skipped) is skipped — nothing to index. Equations get
    their introducing provision statement folded in ("**22.5.1.2**
    Cross-sectional dimensions…") so the chunk carries the provision it
    implements, and a missing section anchor is backfilled from the equation
    key's own dotted prefix (an ACI eq number encodes its section).
    Tables get their caption folded in ("Table 22.5.5.1—…") — the query-
    matchable words their pipe/LaTeX content lacks — and their section is
    backfilled from the table number."""
    header = f"# {items[0]['source_name']}\n"
    # provision statements per page, keyed by their dotted number. Equation
    # chunks fold the matching statement — position-independent, because
    # itemizer re-orders page items by type (equations first, text after).
    stmts = {}
    for it in items:
        if it.get("type") == "text":
            key = _stmt_key(it.get("content") or "")
            if key:
                text = (it.get("content") or "").strip()
                pg = stmts.setdefault(it["page"], {})
                prev = pg.get(key)
                # several statements can share a number (22.5.1.1 appears in
                # both the provision and R-commentary); keep the provision
                # ("shall…") over the commentary ("is assumed…") — iteration
                # order is alphabetical, which picked the commentary before.
                if prev is None or _statement_score(text) > _statement_score(prev):
                    pg[key] = text
    body = []
    # Per-page ordering: each item gets a (numeric_tuple, is_R) sort anchor,
    # inherited from its nearest sectioned same-page predecessor, else from
    # the previous page's last sectioned item (continuation fragments like
    # "where …" / "Notes: …" often start a page under a parent that ended
    # above). True orphans (nothing to inherit) sort to the page tail. Within
    # a section: code, then its R-commentary, then subsections — so R lands
    # directly beneath the section it explains.
    sort_meta = {}
    last_page_key = None
    for pg in sorted({it["page"] for it in items}):
        page_items = _page_order([it for it in items if it["page"] == pg])
        prev_key = last_page_key
        seen = None
        for it in page_items:
            k = _section_key(it)
            if k:
                t = _sort_tuple(k)
                sort_meta[_item_id(it)] = t
                seen = prev_key = t
            elif seen:  # same-page fragment: rides with the last section
                sort_meta[_item_id(it)] = seen
            elif prev_key:  # continuation from the previous page
                sort_meta[_item_id(it)] = prev_key
            else:
                sort_meta[_item_id(it)] = None
        last_page_key = prev_key

    def _render_key(it):
        t = sort_meta.get(_item_id(it))
        if t is None:
            return (1, (), False, _item_order(it))  # keyless -> page tail
        numt, is_r = t
        return (0, numt, is_r, _item_order(it))

    for it in sorted(items, key=lambda x: (x["page"], _render_key(x))):
        content = (it.get("content") or "").strip()
        if not content:
            continue
        title = f"## page {it['page']} {it['type']}"
        # equation number is the resolvable cross-reference key ("eq(22.5.1.10a)")
        if it.get("type") == "equation" and it.get("eq_num"):
            title += f" · eq({it['eq_num']})"
        # section is the full dotted heading ("5.3", "22.5.1.2"); chapter is
        # only a context fallback when no dotted heading was stamped. Equation
        # keys encode their own section, and a table number does too — both
        # backfill missing anchors.
        sec = it.get("section") or it.get("chapter")
        if sec is None and it.get("type") == "equation":
            sec = _eq_section(it.get("eq_num"))
        if sec is None and it.get("type") == "table" and it.get("table_number"):
            sec = it["table_number"]
        if sec is None and it.get("type") == "text":
            sec = _stmt_key(content)  # bold-marker statements sort by this too
        if sec:
            title += f" — {sec}"
        fold = ""
        if it.get("type") == "equation":
            key = _eq_section(it.get("eq_num"))
            if key:
                # the introducing provision may start on the previous page
                for pg in (it["page"], it["page"] - 1):
                    fold = stmts.get(pg, {}).get(key, "")
                    if not fold:  # sub-lettered eq: try the parent provision
                        fold = stmts.get(pg, {}).get(_eq_parent(key) or "", "")
                    if fold:
                        break
        header_note = f"\n\n{fold}" if fold else ""
        chunk = content
        if it.get("type") == "table":
            # ONE canonical representation per table: caption + the normalized
            # readable table (clean-Unicode cells). The raw LaTeX pipe mirror
            # is dropped so the model never has to reconcile two copies of the
            # same table (dedupe).
            tc = _table_prose(content)
            chunk = (f"{it['caption']}\n\n" if it.get("caption") else "") + (tc or content)
            # inline local definitions for symbols this table actually uses
            # (defined elsewhere in ACI) so the model stops hedging on them
            defs = _inline_symbol_defs(chunk)
            if defs:
                chunk += "\n\nSymbols: " + "; ".join(defs)
        body.append(f"{title}{header_note}\n\n{chunk}")
    return header + "\n\n".join(body) + "\n"


def _multipart(field, filename, data):
    boundary = "----raguploader" + os.urandom(8).hex()
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            "Content-Type: text/markdown\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), \
        f"multipart/form-data; boundary={boundary}"


def upload_doc(kb_id, filename, text, dry_run):
    if dry_run:
        print(f"[dry-run] would upload {filename} ({len(text.encode())} chars)")
        return
    body, ctype = _multipart("file", filename, text.encode())
    resp = _api("POST", f"/api/rag/knowledge-bases/{kb_id}/documents",
                data=body, headers={"Content-Type": ctype})
    print(f"uploaded {filename}: {json.dumps(resp)[:160]}")


def _selftest():
    """Runnable check: grouping, ordering, empty-skip, section formatting."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "a-p1-i0.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i0", "page": 1, "type": "equation",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "eq_num": "22.5.1.2", "content": "V_u \\leq \\phi(V_c + 0.66\\sqrt{f_c'}b_w d)"}))
        (td / "a-p1-i1.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i1", "page": 1, "type": "text",
             "chapter": "5", "section": "5.3", "source_name": "a.pdf",
             "content": "first"}))
        (td / "a-p1-i2.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i2", "page": 1, "type": "text",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "content": ""}))  # cleaned-to-empty: must be skipped
        (td / "a-p1-i3.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i3", "page": 1, "type": "text",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "content": "22.5.1.1 In a member without shear reinforcement, shear is assumed to be resisted by the concrete."}))
        (td / "a-p1-i4.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i4", "page": 1, "type": "text",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "content": "**22.5.1.1** Nominal one-way shear strength at a section, $V_n$, shall be calculated by:"}))
        (td / "a-p1-i5.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i5", "page": 1, "type": "equation",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "eq_num": "22.5.1.1", "content": "V_n = V_c + V_s"}))
        (td / "a-p1-i6.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i6", "page": 1, "type": "text",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "content": "**R22.5.1.2** The limit on cross-sectional dimensions is commentary."}))
        (td / "a-p1-i7.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i7", "page": 1, "type": "text",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "content": "22.5.6.3 For prestressed members, $V_c$ shall be permitted to be the lesser of $V_{ci}$ and $V_{cw}$."}))
        (td / "a-p1-i8.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i8", "page": 1, "type": "equation",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "eq_num": "22.5.6.3.1c", "content": "V_{ci} = 0.17\\lambda \\sqrt{f'_c}b_wd"}))
        # page 2: code section, its R-commentary, a section-less fragment that
        # must inherit the R (same-page predecessor), and a subsection
        (td / "a-p2-i9.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p2-i9", "page": 2, "type": "text",
             "chapter": None, "section": "22.5.4", "source_name": "a.pdf",
             "content": "22.5.4 Composite concrete members"}))
        (td / "a-p2-i10.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p2-i10", "page": 2, "type": "text",
             "chapter": None, "section": "R22.5.4", "source_name": "a.pdf",
             "content": "R22.5.4 Composite concrete members commentary"}))
        (td / "a-p2-i11.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p2-i11", "page": 2, "type": "text",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "content": "This fragment continues the commentary."}))
        (td / "a-p2-i12.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p2-i12", "page": 2, "type": "text",
             "chapter": None, "section": "22.5.4.1", "source_name": "a.pdf",
             "content": "22.5.4.1 shall apply to separate placements."}))
        (td / "a-p2-i13.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p2-i13", "page": 2, "type": "text",
             "chapter": None, "section": "R22.5.4.1", "source_name": "a.pdf",
             "content": "R22.5.4.1 The scope includes composite members."}))
        (td / "b-p3-i4.json").write_text(json.dumps(
            {"doc_id": "b", "item_id": "b-p3-i4", "page": 3, "type": "table",
             "chapter": None, "section": None, "source_name": "b.pdf",
             "table_number": "4.1.1", "caption": "Table 4.1.1—Cap",
             "content": "|x|y|"}))
        (td / "b-p3-i5.json").write_text(json.dumps(
            {"doc_id": "b", "item_id": "b-p3-i5", "page": 3, "type": "table",
             "chapter": None, "section": None, "source_name": "b.pdf",
             "table_number": "22.5.5.1", "caption": "Table 22.5.5.1—Vc",
             "content": "|$$0.66\\lambda_s\\lambda(\\rho_w)^{1/3}\\sqrt{f_{c}^{\\prime}}+\\frac{N_{u}}{6A_{g}}$$|"}))
        (td / "junk.json").write_text("{corrupt")
        by_doc = docs_from_verified(td)
        assert set(by_doc) == {"a", "b"}, by_doc  # corrupt file skipped
        md_a = render_markdown(by_doc["a"])
        assert "# a.pdf" in md_a and "— 5.3" in md_a, md_a  # section is full dotted path
        assert "5.5.3" not in md_a
        assert "second" not in md_a and "empty" not in md_a  # empty item skipped
        # sub-lettered equation folds its PARENT provision (22.5.6.3.1c <- 22.5.6.3)
        sub_chunk = md_a.split("## page 1 equation · eq(22.5.6.3.1c) — 22.5.6.3.1")[1].split("## page 1 text")[0]
        assert "22.5.6.3 For prestressed members" in sub_chunk, sub_chunk
        assert _eq_parent("22.5.6.3.1") == "22.5.6.3" and _eq_parent("22.5.1.2") == "22.5.1"
        assert _eq_parent("22.1") == "22"

        assert _eq_section("22.5.6.3.1a") == "22.5.6.3.1" and _eq_section("R22.5.6.2") == "R22.5.6.2"
        # per-page ordering: code, then its R, then the subsection (R directly
        # beneath its code), and a section-less fragment rides with the R it
        # follows in document order
        frame = [x.split("\n\n")[0] for x in md_a.split("## ") if x]
        p2 = [x for x in frame if x.startswith("page 2 ")]
        # order: code 22.5.4 -> R22.5.4 -> fragment (title has no section, but
        # sorts under R22.5.4) -> 22.5.4.1 -> R22.5.4.1
        tails = [t.rsplit(" — ", 1)[-1] if " — " in t else "" for t in p2]
        assert tails == ["22.5.4", "R22.5.4", "", "22.5.4.1", "R22.5.4.1"], p2
        assert _eq_section(None) is None and _eq_section("") is None
        # R-commentary has no key, so _stmt_key returns None for it
        assert _stmt_key("**R22.5.1.2** The limit on cross") is None
        md_b = render_markdown(by_doc["b"])
        # table: no section -> backfilled from table_number; caption folded in;
        # the normalized (clean-Unicode) table is the single representation,
        # no raw LaTeX pipe mirror. x:y table keeps its structure.
        assert "## page 3 table — 4.1.1\n\nTable 4.1.1—Cap\n\n| x | y |" in md_b, md_b
        assert "4.1.1" in md_b
        # math-heavy table: Unicode normalize (λ_s both lambdas distinct),
        # superscripts flattened, and inline symbol defs for A_g / N_u
        assert "0.66λ_sλ(ρ_w)1/3" in md_b, md_b
        assert "√(f_c′" in md_b and "(N_u)/(6A_g)" in md_b, md_b
        assert "Symbols: A_g = gross area" in md_b and "N_u = factored axial force" in md_b, md_b
        assert "lambdas" not in md_b and "rhow" not in md_b and "sqrt(" not in md_b, md_b
    # subscripts survive _math_to_text (Unicode): λ_s / ρ_w stay distinct
    # tokens — regression guard for the KB prose λ-drop bug (stripped "_"
    # used to merge them into unsegmentable lambdas/rhow)
    m = _math_to_text("\\lambda_s\\lambda(\\rho_w)^{1/3}")
    assert "λ_sλ" in m and "ρ_w" in m, m
    assert "lambdas" not in m and "rhow" not in m and "sqrt" not in m, m
    # braced subscripts (two-way Table 22.6.5.2 path) also stay segmentable
    mb = _math_to_text("0.33\\lambda_{s}\\lambda\\sqrt{f_{c}^{\\prime}}")
    assert "λ_sλ√(f_c′" in mb, mb
    assert "lambdas" not in mb and "sqrt" not in mb, mb
    print("selftest: ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default=DEFAULT_KB, help="knowledge base name (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true", help="render + report, upload nothing")
    ap.add_argument("--selftest", action="store_true", help="run offline self-check and exit")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return
    if "UNSLOTH_API_KEY" not in os.environ:
        raise SystemExit("UNSLOTH_API_KEY not set")

    by_doc = docs_from_verified(VERIFIED_DIR)
    if not by_doc:
        raise SystemExit(f"no verified items in {VERIFIED_DIR}")
    kb_id = None if args.dry_run else get_or_create_kb(args.kb)
    for doc_id, items in sorted(by_doc.items()):
        upload_doc(kb_id, f"{doc_id}.md", render_markdown(items), args.dry_run)
    if not args.dry_run:
        kbs = _api("GET", "/api/rag/knowledge-bases").get("knowledgeBases", [])
        kb = next((k for k in kbs if k["id"] == kb_id), None)
        print(f"KB {args.kb!r}: {kb.get('documentCount') if kb else 'unknown'} documents")


if __name__ == "__main__":
    main()