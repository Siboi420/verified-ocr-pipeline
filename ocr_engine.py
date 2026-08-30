#!/usr/bin/env python3
"""
OCR engine for structural/seismic documents using local GLM-OCR via Unsloth Studio.
Supports PDF and image inputs, concurrent page processing, markdown output.

Usage:
    python3 ocr_engine.py page.png                    # Single image
    python3 ocr_engine.py doc.pdf                     # All pages
    python3 ocr_engine.py doc.pdf --pages 5-15        # Page range (1-indexed)
    python3 ocr_engine.py doc.pdf --workers 4         # 4 concurrent workers
    python3 ocr_engine.py doc.pdf --output result.md  # Save to file
"""

import argparse
import base64
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from config import API_BASE, API_KEY, MODEL

OCR_PROMPT = (
    "Extract all text, tables, and numbers from this document page as clean markdown. "
    "Preserve all numerical values exactly. "
    "Format tables with markdown table syntax (| column1 | column2 |). "
    "Do not summarize or paraphrase."
)

# Dedicated prompt for caption-band crops: GLM likes to table-ify (or empty)
# tiny regions under the generic prompt, which makes it drop the caption line.
CAPTION_BAND_PROMPT = (
    "Transcribe the text in this image as plain text lines. "
    "Do NOT use markdown or HTML table syntax. "
    "If it is a table caption, start it with 'Table'."
)


# ── Image Encoding ──────────────────────────────────────────────────────────

def encode_image(image_path):
    """Read an image file and return its base64 data URI."""
    try:
        data = Path(image_path).read_bytes()
    except OSError as e:
        raise OSError(f"cannot read image {image_path}: {e}") from e
    ext = Path(image_path).suffix.lower().lstrip(".")
    mime = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
    }.get(ext, "image/png")
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"


# ── PDF → Images ────────────────────────────────────────────────────────────

def pdf_to_images(pdf_path, dpi=200, page_range=None):
    """Convert PDF pages to PNGs via PyMuPDF. Returns (images, tmpdir)."""
    import pymupdf

    doc = pymupdf.open(pdf_path)
    total = len(doc)

    if page_range:
        start, end = page_range
        pages = range(max(1, start), min(total, end) + 1)
    else:
        pages = range(1, total + 1)

    images = []
    tmpdir = Path(pdf_path).parent / f".ocr_tmp_{Path(pdf_path).stem}"
    tmpdir.mkdir(parents=True, exist_ok=True)

    for page_num in pages:
        page = doc[page_num - 1]
        mat = pymupdf.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_path = tmpdir / f"page_{page_num:04d}.png"
        pix.save(str(img_path))
        images.append((page_num, str(img_path)))

    doc.close()
    return images, str(tmpdir)


# ── OCR a single page ───────────────────────────────────────────────────────

def tesseract_ocr(image_path, lang="eng"):
    """OCR a single image with the system tesseract CLI (no pip dependency).

    Returns {"text": ...}. Raises RuntimeError if tesseract is not installed
    or fails.
    """
    import shutil
    import subprocess

    if shutil.which("tesseract") is None:
        raise RuntimeError(
            "tesseract not found on PATH: install tesseract-ocr to use the "
            "Tesseract caption engine (GLM stays the default)"
        )
    proc = subprocess.run(
        ["tesseract", str(image_path), "stdout", "-l", lang],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"tesseract failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}"
        )
    return {"text": proc.stdout.strip()}


def _require_key():
    """Fail early with a clear message when running a real OCR call without a key."""
    if not API_KEY:
        raise RuntimeError("UNSLOTH_API_KEY not set. Create it with your Unsloth Studio API key.")
    return API_KEY


def ocr_page(image_path, page_num=None, max_tokens=4096, prompt=OCR_PROMPT):
    """Send a page image to GLM-OCR. Returns dict with text + metadata."""
    data_uri = encode_image(image_path)
    label = f"page {page_num}" if page_num else Path(image_path).name

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
    }

    t0 = time.time()
    resp = requests.post(
        f"{API_BASE}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {_require_key()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=600,
    )
    elapsed = time.time() - t0
    resp.raise_for_status()
    data = resp.json()

    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    truncated = data["choices"][0].get("finish_reason") == "length"

    if truncated:
        # Retry with doubled token budget
        new_max = max_tokens * 2
        print(f"  [{label}] truncated at {max_tokens}, retrying with {new_max}")
        payload["max_tokens"] = new_max
        t0 = time.time()
        resp = requests.post(
            f"{API_BASE}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_require_key()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=600,
        )
        elapsed = time.time() - t0
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        truncated = data["choices"][0].get("finish_reason") == "length"

    result = {
        "page": page_num,
        "path": image_path,
        "text": content.strip(),
        "elapsed_s": round(elapsed, 1),
        "truncated": truncated,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
    }

    print(f"  [{label}] {elapsed:.1f}s  {usage.get('completion_tokens', 0)} tok  "
          f"{'[TRUNCATED]' if truncated else ''}")
    return result


# ── Batch OCR ────────────────────────────────────────────────────────────────

def ocr_batch(images, workers=2, max_tokens_per_page=4096):
    """
    OCR multiple pages concurrently.
    images: list of (page_num, image_path)
    Returns list of result dicts in page order.
    """
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(ocr_page, path, num, max_tokens_per_page): num
            for num, path in images
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r["page"] if r["page"] else 0)
    return results


# ── Assembly ─────────────────────────────────────────────────────────────────

def assemble_markdown(results, source_name=""):
    """Merge OCR results into a single markdown document."""
    parts = []
    if source_name:
        parts.append(f"# OCR: {source_name}\n")

    for r in results:
        label = f"--- Page {r['page']} ---" if r["page"] else f"--- {Path(r['path']).name} ---"
        parts.append(f"\n{label}\n")
        parts.append(r["text"])
        if r["truncated"]:
            parts.append(f"\n*[Page {r['page'] or ''}: output truncated]*")

    return "\n".join(parts)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OCR documents using local GLM-OCR via Unsloth Studio"
    )
    parser.add_argument("input", help="PDF or image file path")
    parser.add_argument("--pages", help="Page range (e.g. 5-15, 1-indexed)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Concurrent workers (default: 2)")
    parser.add_argument("--dpi", type=int, default=200,
                        help="PDF render DPI (default: 200)")
    parser.add_argument("--max-tokens", type=int, default=4096,
                        help="Max tokens per page (default: 4096, auto-retries if truncated)")
    parser.add_argument("--output", "-o", help="Output markdown file (default: stdout)")

    args = parser.parse_args()
    input_path = args.input

    if not os.path.exists(input_path):
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    ext = Path(input_path).suffix.lower()

    if ext == ".pdf":
        print(f"Converting PDF: {input_path}  (dpi={args.dpi})")
        page_range = None
        if args.pages:
            try:
                parts = args.pages.split("-")
                page_range = (int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                print("Error: invalid page range (use e.g. 5-15)", file=sys.stderr)
                sys.exit(1)
            print(f"  Pages: {parts[0]}-{parts[1]}")

        images, tmpdir = pdf_to_images(input_path, dpi=args.dpi, page_range=page_range)
        print(f"  {len(images)} pages to process")

        if not images:
            print("No pages in range.")
            return

        print(f"OCR with {args.workers} workers...")
        results = ocr_batch(images, workers=args.workers,
                            max_tokens_per_page=args.max_tokens)

        # Cleanup temp images
        for _, img_path in images:
            try:
                os.remove(img_path)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    else:
        # Single image
        print(f"OCR image: {input_path}")
        result = ocr_page(input_path, max_tokens=args.max_tokens)
        results = [result]

    # Assemble and output
    markdown = assemble_markdown(results, source_name=Path(input_path).name)

    if args.output:
        try:
            Path(args.output).write_text(markdown)
        except OSError as e:
            print(f"Error: cannot write {args.output}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"\nWrote {len(markdown):,} chars to {args.output}")
    else:
        print("\n" + "=" * 60)
        print(markdown)

    total_tok = sum(r["tokens_out"] for r in results)
    total_time = sum(r["elapsed_s"] for r in results)
    n = len(results)
    print(f"\n{'=' * 60}")
    print(f"Pages: {n} | Total tokens: {total_tok} | Total time: {total_time:.0f}s | "
          f"Avg: {total_time/n:.1f}s/page" if n else "")


if __name__ == "__main__":
    main()