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


def render_markdown(items):
    """One markdown doc per source file. Empty content (boilerplate-only
    items are skipped) is skipped — nothing to index. Equations get
    their introducing provision statement folded in ("**22.5.1.2**
    Cross-sectional dimensions…") so the chunk carries the provision it
    implements, and a missing section anchor is backfilled from the equation
    key's own dotted prefix (an ACI eq number encodes its section)."""
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
    for it in sorted(items, key=lambda x: (x["page"], _item_order(x))):
        content = (it.get("content") or "").strip()
        if not content:
            continue
        title = f"## page {it['page']} {it['type']}"
        # equation number is the resolvable cross-reference key ("eq(22.5.1.10a)")
        if it.get("type") == "equation" and it.get("eq_num"):
            title += f" · eq({it['eq_num']})"
        # section is the full dotted heading ("5.3", "22.5.1.2"); chapter is
        # only a context fallback when no dotted heading was stamped. Equation
        # keys encode their own section, so backfill missing anchors from them.
        sec = it.get("section") or it.get("chapter")
        if sec is None and it.get("type") == "equation":
            sec = _eq_section(it.get("eq_num"))
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
        body.append(f"{title}{header_note}\n\n{content}")
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
        (td / "b-p3-i4.json").write_text(json.dumps(
            {"doc_id": "b", "item_id": "b-p3-i4", "page": 3, "type": "table",
             "chapter": None, "section": "4.1.1", "source_name": "b.pdf",
             "content": "|x|y|"}))
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
        assert _eq_section(None) is None and _eq_section("") is None
        # R-commentary has no key, so _stmt_key returns None for it
        assert _stmt_key("**R22.5.1.2** The limit on cross") is None
        md_b = render_markdown(by_doc["b"])
        assert "4.1.1" in md_b and "|x|y|" in md_b
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