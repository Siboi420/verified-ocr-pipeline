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
import urllib.error
import urllib.request
from pathlib import Path

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


def render_markdown(items):
    """One markdown doc per source file. Empty content (boilerplate-only
    items that cleaned to '') is skipped — nothing to index."""
    header = f"# {items[0]['source_name']}\n"
    body = []
    for it in sorted(items, key=lambda x: (x["page"], x["item_id"])):
        content = (it.get("content") or "").strip()
        if not content:
            continue
        title = f"## page {it['page']} {it['type']}"
        # equation number is the resolvable cross-reference key ("eq(22.5.1.10a)")
        if it.get("type") == "equation" and it.get("eq_num"):
            title += f" · eq({it['eq_num']})"
        # section is the full dotted heading ("5.3", "4.1.1"); chapter is
        # only a context fallback when no dotted heading was stamped.
        if it.get("section"):
            title += f" — {it['section']}"
        elif it.get("chapter"):
            title += f" — {it['chapter']}"
        body.append(f"{title}\n\n{content}")
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
        (td / "a-p1-i1.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i1", "page": 1, "type": "text",
             "chapter": "5", "section": "5.3", "source_name": "a.pdf",
             "content": "first"}))
        (td / "a-p1-i2.json").write_text(json.dumps(
            {"doc_id": "a", "item_id": "a-p1-i2", "page": 1, "type": "text",
             "chapter": None, "section": None, "source_name": "a.pdf",
             "content": ""}))  # cleaned-to-empty: must be skipped
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