# Validation App — Current State + Fixes Needed

## What exists

The Flask validation app at ~/Projects/seismic-ai-tools/ is built and running at http://127.0.0.1:5000. It has:

- app.py — Flask routes, OCR trigger, page rendering, item actions
- config.py — shared config (API_BASE, API_KEY from env, UPLOAD_DIR)
- itemizer.py — parses OCR markdown into page-scoped items (table/equation/text)
- ocr_engine.py — GLM-OCR wrapper (API key now env-only, no hardcoded fallback)
- templates/index.html — single-page JS UI
- test_itemizer.py — 17 assertions, all pass
- smoke_test.py — 20 integration checks, all pass

## What works

- Load existing PDF + markdown by path -> parses items, shows review page
- Upload PDF with markdown -> parses and redirects to review
- Page PNG rendering (PyMuPDF, cached)
- Item actions: Accept, Edit+Accept (editable table grid), Reject, Skip
- Bulk pass-for-now / accept
- After-action navigation (advances to next item of same type on same page)
- Async OCR with job polling (thread + status check)

## What is broken: Upload without markdown (OCR not triggering)

### Problem

When a user uploads a PDF alone (no pre-OCRed markdown):

1. /upload saves the PDF, creates doc with empty pages, redirects to /doc/doc_id
2. The review page shows the PDF on the left but has no items on the right
3. OCR never starts automatically

### Required fix

Add auto-OCR to the doc view template. When the page loads and DOC.pages is empty or has no items, automatically start the OCR job and poll until done.

In templates/index.html, add this to the script section (inside the {% if doc %} block, after the existing initialization):

```javascript
// Auto-start OCR if doc has no items
(async function autoOcr() {
  const hasItems = (DOC.pages || []).some(p => (p.items || []).length > 0);
  if (hasItems) return;
  const bar = document.getElementById("ocrbar");
  if (!bar) return;
  try {
    bar.style.display = "block";
    bar.className = "err"; // actually informational, use blue
    bar.textContent = "OCR in progress…";
    const {job_id} = await post(`/ocr/${DOC.doc_id}`);
    await pollJob(job_id, () => {
      // Reload the document state after OCR completes
      fetch(`/doc/${DOC.doc_id}`).then(r => r.text()).then(html => {
        // Extract the embedded DOC JSON from the page
        const m = html.match(/const DOC = ([^;]+);/);
        if (m) Object.assign(DOC, JSON.parse(m[1]));
        bar.style.display = "none";
        render();
      });
    });
  } catch (e) {
    bar.textContent = "OCR failed: " + e.message;
  }
})();
```

Also update the `/upload` route redirect to pass a flag so the template knows OCR is needed:

In app.py /upload route, when there is no markdown file uploaded, redirect to:
```python
redirect(f"/doc/{doc_id}?ocr=1")
```

In the template, check URL params and only auto-run OCR if `?ocr=1` is present. This prevents auto-OCR from firing when loading an existing document that simply has no items.

```javascript
const needsOcr = new URLSearchParams(window.location.search).get("ocr") === "1";
if (needsOcr) autoOcr();
```

## Files to modify

1. templates/index.html — add autoOcr() function and ?ocr=1 check
2. app.py — add ?ocr=1 to redirect when no markdown provided