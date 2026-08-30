# AGENTS.md — Seismic AI Tools (`~/Projects/seismic-ai-tools`)

Context file for AI assistants. Read fully before working in this project.

## ⛔ Mandatory: keep this file in sync with committed changes

**Every commit that changes behavior, file layout, routes, item shapes, tooling,
or test counts MUST update this file in the same commit.** An AGENTS.md that lies
or lags causes wrong assumptions downstream — treat a stale entry as a bug.

Before committing, check these sections against the diff and update what moved:
- `What this project is` / `Architecture / data flow` — new routes, scripts, files,
  data-flow steps.
- Per-file bullets — new functions/behaviors, renamed or retired logic.
- `Current state (verified facts)` — facts that changed or were resolved;
  remove or rewrite resolved open questions.
- `Known gotchas / foot-guns` — new failure modes you hit.
- `Tests / verification` — assertion counts (count actual `check(` calls),
  commands that changed, new test files.

A commit that touches code but not AGENTS.md is incomplete. (This rule itself is
the result of a commit that forgot it once.)

## What this project is

A local OCR-validation toolkit for structural/seismic engineering research (Kresna's
MEXT/SSI-lateral research). **Source PDF → GLM-OCR → human review → verified data.**

Two things live here:

1. **Validation app** — Flask web UI (`localhost:5000`) for reviewing GLM-OCR output
   item-by-item (tables, equations, text), accepting/editing/rejecting, and writing
   verified JSON.
2. **Supporting tooling** — `ocr_engine.py` (GLM-OCR client), `itemizer.py` (OCR
   markdown → review items), `rag_uploader.py` (verified JSON → Unsloth RAG KB),
   config, schemas (empty), docs.

Related but separate: `~/Projects/StructuralEngineeringWorkspace/` (OpenSeesPy research,
has its own AGENTS.md). This app is the human-review step feeding that research.

## Services / runtime (how it actually runs)

- **Flask app**: `python3 app.py` → `http://127.0.0.1:5000`, single-user local tool.
  Requires `UNSLOTH_API_KEY` env var (no hardcoded fallback; the key lives in
  `.env.local`, gitignored — this AGENTS.md is git-tracked and pushed to GitHub,
  so the key must never go in here). There is **no auto-restart supervisor** — if
  the app is down, restart it in TWO separate shell commands:
  ```bash
  pkill -f "python3 app\.py" || true                      # kill
  cd ~/Projects/seismic-ai-tools && set -a; . ./.env.local; set +a; nohup python3 app.py > /tmp/seismic-app.log 2>&1 &   # start
  ```
  **Foot-gun (verified):** `pkill -f "python3 app.py"` matches its own command
  line, so kill + start in ONE shell command kills the shell before the start runs.
  Use two separate invocations, and escape the dot (`app\.py`) so the pattern
  doesn't match the pkill argument itself.
- **Unsloth Studio**: `http://127.0.0.1:8888`, serves the GLM-OCR model via OpenAI-style
  `/v1/chat/completions`. Auth via `Authorization: Bearer <UNSLOTH_API_KEY>`.
  - Model: `ggml-org/GLM-OCR-GGUF` (Q8_0 as of last swap). Loaded via:
    `POST /api/inference/load {"model_path":"ggml-org/GLM-OCR-GGUF","force_reload":true}`
  - **Model unloads** on `POST /api/inference/unload` (or `{"force_cancel_active":true}`
    when stuck gens block it). After unload, OCR errors with "No model loaded" until reloaded.
  - **Backend is CUDA now**: `POST /api/llama/backend {"backend":"cuda"}` switches the
    llama.cpp build (WSL2 RTX 2080 Ti 11GB, `/dev/dxg`). GPU offload verified
    (100% util, ~2.6s/inference vs 10-15s CPU).
- **WSL2, RTX 2080 Ti 11GB**, Python 3, Flask, PyMuPDF. Deps in `requirements-ocr.txt`.

## Architecture / data flow

```
Source PDF -> /upload (PDF-only -> auto-OCR via ?ocr=1; optional ocr_pages="2-3, 4-9"
  page ranges for the initial OCR run) or /load (paths to pdf+md)
  -> GLM-OCR (Unsloth :8888) -> markdown -> itemizer.py -> review items
  -> human Accept/Edit/Reject/Skip
  -> validation/verified/ + validation/rejected/ (JSON per item)
```

- `app.py` — Flask routes: `/`, `/load` (paths), `/upload` (multipart, optional md,
  optional `ocr_pages` form field), `/ocr/<doc_id>` (async job), `/jobs/<id>` (poll,
  live `done`/`total` page counts), `/doc/<id>` (review page),
  `/page/<doc_id>/<n>.png` (rendered, cached), `/item/.../action`, `/bulk`.
  Jobs are an **in-memory dict** — lost on restart. `upload()` validates `ocr_pages`
  (`N`, `N-M`, or comma-separated like `2-3, 4-9`, 1-indexed; invalid -> 400) and
  stores `doc["ocr_pages"]` as a list of `[start, end]` pairs (e.g. `[[2,3],[4,9]]`;
  legacy flat `[5,10]` still accepted) only in the no-markdown branch (md uploads
  ignore the field). `_run_ocr` passes the stored value straight to `pdf_to_images`
  (clamps, merges), errors clearly if zero pages match, and pushes `done`/`total`
  into the job dict via `ocr_batch(on_progress=...)`. bbox/caption routes parse box
  coords through `_parse_box`, rejecting non-finite (NaN/Inf) values with 400.
- `rag_uploader.py` — reads `validation/verified/*.json`, groups items by
  `doc_id`, renders one markdown file per source doc (`source_name` header, per-item
  `## page N <type> — <section>` titles, section is the full dotted heading)
  and uploads to an Unsloth Studio RAG KB (`/api/rag/knowledge-bases`, name via
  `--kb`, default "Verified OCR"). Server-side chunking + embeddings. Skips
  unreadable JSON **and stale pre-metadata exports** (missing `source_name` — the
  old bbox-OCR artifacts) with warnings. `--selftest` for offline checks,
  `--dry-run` to render without uploading. Requires `UNSLOTH_API_KEY`.
- `config.py` — `API_BASE` (default `http://localhost:8888`), `API_KEY` (env-only),
  `MODEL = "ggml-org/GLM-OCR-GGUF"`, `UPLOAD_DIR`.
- `ocr_engine.py` — `pdf_to_images(dpi=200, page_range=None)` (accepts a single
  `(s, e)` or a list of `(s, e)` pairs; clamped to the PDF, overlaps merged),
  `parse_page_range(s)` (`"N"`/`"N-M"` -> tuple or `None`) and `parse_page_ranges(s)`
  (comma-separated `2-3, 4-9` -> list of tuples or `None`; used by the app upload,
  CLI `--pages` uses the single-range form),
  `ocr_page`/`ocr_batch(workers=2, on_progress=None)` (`on_progress(done, total)`
  fires after each completed page), `assemble_markdown`. Timeouts were raised to
  600s (GLM-OCR f16-on-CPU read timed out at 120s; now GPU so fine). Payload is
  non-streaming chat completions — see note below.
- `itemizer.py` — splits markdown on `--- Page N ---`, extracts equations
  (`$$...$$` / `\[...\]`), **pipe tables and HTML `<table>...</table>`** (added to fix
  GLM-OCR answering HTML tables; HTML converted to `|...|` markdown), inline math
  (`\(...\)` / `$...$`). Item order: equation → table → text-math → text, per page.
  Every item carries `chapter`/`section` (nearest preceding `CHAPTER N` / dotted
  heading, `R21.2.1` kept, both reset per page; line-start anchored so inline
  refs/decimals never match). `clean_export_text()` strips `# OCR:` titles and
  standalone CODE/COMMENTARY markers — applied **at export only** (verified JSON,
  not the review UI/doc).
- `templates/index.html` — single-page JS UI. Has an `autoOcr()` + `?ocr=1` gate
  (upload PDF-only → auto-starts OCR). Upload form has an optional `ocr_pages` text
  input. `post()`, `pollJob(jobId, done, progress)` helpers (progress callback
  updates the line while running). Auto-OCR bar and upload status show live
  `D/T pages`. Header shows an "OCR'd X/Y pages" coverage line (pages with ≥1 item
  over `n_pages`), set by `render()`, which is now also called once on doc-page
  load (the page selector/items previously only rendered after a user interaction).
  The item counter always reflects the current page — pages with no items (e.g.
  outside the OCR page range) show "no items on page N", never a stale count from
  a previously-viewed page.
  An "OCR'd pages only" checkbox (`onlyOcr`) restricts `#pageSel` and prev/next to
  pages in `DOC.pages` (pages with ≥1 item; falls back to all pages when there are
  none), via `navPages()` (filtered list or full 1..n) and `stepPage(dir)` (steps the
  same list, defaulting to first/last when off it). When the filter is on, the page
  selector and `#pageTotal` show the *position within the OCR'd pages* (`pageLabel()`,
  e.g. "2 of 3") instead of the absolute page number; `#pgInfo` keeps the actual page
  for reference. Two per-page bulk buttons reuse
  the existing `/bulk` accept route with computed `item_ids`: `bulkAcceptMath`/
  `bulkAcceptText` (→ `bulkAcceptType(kind)`) accept every current-page text item
  with/without `has_inline_math` that isn't verified/rejected (works regardless of
  the showMath/showText toggles; alerts "nothing to accept on this page" when empty).
  **Note:** `location.reload()` refreshes DOC after OCR job completes (deliberate;
  the old inline `const DOC = ([^;]+);` regex broke on `;` in content).
  Accepted/rejected items are visually distinct: `.item.done` dims, `.item.verified`
  gets a green border/tint, `.item.rejected` a red one (status is also in the meta text).
- `validation/` — `pending/` (docs), `verified/`, `rejected/` (per-item JSON), `uploads/<doc_id>/` (pdf, md, page PNGs).

## Current state (verified facts — don't contradict)

- GLM-OCR runs on **GPU** (CUDA llama.cpp build, `-ngl -1` = auto-offload, not CPU-only).
- Timeouts in `ocr_engine.py` are 600s.
- Itemizer handles **HTML tables** (GLM-OCR sometimes emits HTML instead of markdown).
- Auto-OCR on PDF-only upload works (blue bar, polls, reloads, `?ocr=1`).
- "OCR'd pages only" filter + per-page bulk accept (inline math / text) work via
  the existing `/bulk` route — no backend changes.
- Upload accepts an optional `ocr_pages` range — single (`5`, `1-3`) or
  comma-separated (`2-3, 4-9`); stored on the pending doc JSON as a list of
  `[start, end]` pairs and honored by the initial OCR run only (re-OCR of extra
  pages out of scope). Invalid values return 400.
- OCR jobs report live `done`/`total` page counts, surfaced in the auto-OCR bar,
  the upload status line, and the review-page "OCR'd X/Y pages" line.
- `testOCR` doc: page 1 cost table (HTML→markdown) + page 3 pipe table both recognized.
  **Open question:** user reports page 1 may contain a 2nd table visually that GLM-OCR
  doesn't extract; investigation was in progress (GLM-OCR directed at page 1 returns only
  the cost table). Not resolved.
- Jobs dict is in-memory (restart loses jobs). `UNSLOTH_API_KEY` is required.

## Known gotchas / foot-guns

- **Model unload state**: if OCR suddenly errors "No model loaded", reload via
  `POST /api/inference/load {"model_path":"ggml-org/GLM-OCR-GGUF"}`.
- **Active generations**: non-streaming chat calls (what ocr_engine.py sends) are NOT
  cancellable via `POST /api/inference/cancel` (returns `cancelled: 0`, they're not
  registered). The working kill is unload with `force_cancel_active: true`. If you need
  cancelable OCR, add `"stream": true` to the payload.
- **App respawns**: don't assume a `kill`/`pkill` sticks — verify with `curl`.
- **WSL GPU**: `/dev/dxg` is the paravirtualized path; `nvidia-smi` works via
  `/usr/lib/wsl/lib/nvidia-smi`. Unsloth's `POST /api/llama/backend` is the supported
  way to change llama.cpp build (cpu/cuda/rocm/vulkan).
- Do not paste the API key into code; it lives in `.env.local` (gitignored) and env only.
- Do not fabricate results; verify against actual tool output (tests, curl, logs).

## Tests / verification

- `python3 test_itemizer.py` — 61 itemizer assertions.
- `python3 smoke_test.py` — 75 end-to-end checks via Flask test client (no live OCR;
  asserts `?ocr=1` redirect, no-key OCR error, `parse_page_range`/`parse_page_ranges`
  valid+invalid forms, `ocr_pages` storage incl. multiple ranges, invalid -> 400,
  md-wins-over-range, NaN bbox coords rejected).
- A quick GPU/alive check: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/`
  (app) and `:8888` (unsloth); `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader`
  should show >0% during OCR.