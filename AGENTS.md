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
  `/page/<doc_id>/<n>.png` (rendered, cached), `/item/.../action`, `/item/.../delete`,
  `/bulk`.
  Jobs are an **in-memory dict** — lost on restart. `upload()` validates `ocr_pages`
  (`N`, `N-M`, or comma-separated like `2-3, 4-9`, 1-indexed; invalid -> 400) and
  stores `doc["ocr_pages"]` as a list of `[start, end]` pairs (e.g. `[[2,3],[4,9]]`;
  legacy flat `[5,10]` still accepted) only in the no-markdown branch (md uploads
  ignore the field). `_run_ocr` passes the stored value straight to `pdf_to_images`
  (clamps, merges), errors clearly if zero pages match, and pushes `done`/`total`
  into the job dict via `ocr_batch(on_progress=...)`. bbox/caption routes parse box
  coords through `_parse_box`, rejecting non-finite (NaN/Inf) values with 400.
  `apply_action` (used by `/item/<id>/action`) treats `verified`/`rejected` items as
  final except for an explicit **flip to the other state** (accept↔reject; same-state
  and skip stay no-ops), removes the stale counterpart JSON
  (`verified/`↔`rejected/`) when flipping so the copies never diverge, and carries
  an optional `table_spans` field (merged-cell map for tables) into the item and
  verified/rejected JSON. Accept-with-new-content also updates an already-verified
  item (used when re-editing a merged table). For `equation` items, accept/reject
  payloads also carry `eq_num`/`eq_letters` (the cross-reference key, e.g.
  "22.5.1.10a" + "a") whenever the client sends them: `None` = untouched,
  `""` = clears the key — the key is **never re-derived from the text**
  (the old edit→accept `eq_refs` re-derivation that clobbered a manually-fixed
  key is gone). `/bulk` accepts
  `skip|accept|reject` with the same semantics: skip never touches finalized items,
  accept/reject flip them; already-in-target-state items are no-ops.
  The draw-box route `/bbox_ocr` accepts an optional `type` (`auto` default, or
  `equation`/`table`/`text`, validated with 400 **before** OCR runs): `auto` keeps
  the old parse-the-crop + caption-attach behavior, forced kinds append ONE item
  via `append_bbox_item` (raw OCR text as `content`, no caption attach; `text`
  sets `has_inline_math` from `INLINE_MATH_RE`, `equation` captures
  `eq_num`/`eq_letters` via `eq_refs`, and `chapter`/`section` inherit from the
  page's latest item that carries them). Forced **equation** draws use
  `EQUATION_PROMPT` (LaTeX math only — GLM otherwise wraps equation crops in
  HTML `<table>` artifacts under the generic markdown prompt). `equation`/`text`
  kinds are defensively stripped of HTML table wrapper tags (`HTML_TABLE_TAG_RE`,
  math/content kept; forced `table` draws keep the raw text since the wrapper may
  be the only structure). `/item/<id>/delete` removes an item from
  its page and unlinks both `verified/` and `rejected/` copies (404 if missing).
- `rag_uploader.py` — reads `validation/verified/*.json`, groups items by
  `doc_id`, renders one markdown file per source doc (`source_name` header, per-item
  `## page N <type> — <section>` titles, section is the full dotted heading;
  equation items with `eq_num` get a ` · eq(N)` title suffix so the KB text
  carries the resolvable key). Render-time hardening for equations: a missing
  section is **backfilled from the eq key's own dotted prefix** (an ACI eq number
  encodes its section), and the matching provision statement is **folded into the
  equation chunk** (matched by dotted number across the page, position-independent
  because itemizer re-orders page items by type; falls back to the parent
  provision for sub-lettered equations and to the previous page where a provision
  starts; multiple same-key statements prefer the provision (“shall…”) over
  R-commentary (“is assumed…”); R-commentary equations and unverified
  statements stay unfolded — current ACI export: 43 eq chunks, 30 folded). Items
  order by the numeric index of `item_id` (lexical sort put i10
  before i2)
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
  `ocr_page`/`ocr_batch(workers=2, max_tokens_per_page=8192, on_progress=None)`
  (`on_progress(done, total)`
  fires after each completed page), `assemble_markdown`, and prompt constants
  `OCR_PROMPT`, `CAPTION_BAND_PROMPT` (caption crops — GLM drops/disfigures
  tiny regions under the generic prompt), `EQUATION_PROMPT` (draw-box equation
  mode — output LaTeX math only, no tables/HTML). Timeouts were raised to
  600s (GLM-OCR f16-on-CPU read timed out at 120s; now GPU so fine). Payload is
  non-streaming chat completions — see note below. `max_tokens_per_page` default
  was 4096; most pages exceeded it (finish_reason="length" → re-send at 2×),
  so it's now **8192** (CLI `--max-tokens` default too) — at 16k context this
  is safe and eliminates most truncation retries.
- `itemizer.py` — splits markdown on `--- Page N ---`, extracts equations
  (`$$...$$` / `\[...\]`), **pipe tables and HTML `<table>...</table>`** (added to fix
  GLM-OCR answering HTML tables; HTML converted to `|...|` markdown), inline math
  (`\(...\)` / `$...$`). Item order: equation → table → text-math → text, per page.
  Every item carries `chapter`/`section` (nearest preceding `CHAPTER N` / dotted
  heading, `R21.2.1` kept, both reset per page; line-start anchored so inline
  refs/decimals never match; GLM-OCR bolds provision markers
  (`**22.5.1.2**`), so an optional `**` prefix is tolerated in the section and
  chapter regexes). Equation items additionally may carry
  `eq_letters`/`eq_num`, the reference markers GLM-OCR prints OUTSIDE the
  `$$...$$` span ("(a) $$x$$ (22.5.1.10a)"): `eq_refs(front, back)` grabs a
  leading `(a)` and a trailing `(22.5.1.10a)`-style number (letter suffix
  included), and the reviewer fixes a missing/OCR-dropped number (or clears a
  wrong one) via the editor's dedicated eq-number / eq-letters fields — accept
  never re-derives the key. `clean_export_text()` strips `# OCR:` titles and
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
  for reference. Four per-page bulk buttons reuse
  the existing `/bulk` route with computed `item_ids`: `bulkAcceptMath`/`bulkAcceptText`
  and `bulkRejectMath`/`bulkRejectText` (→ `bulkByType(action, kind)`) accept/reject
  every current-page text item
  with/without `has_inline_math` and flip finalized items like single-item actions (accept
  converts rejected→verified, reject converts verified→rejected; same-state items
  skipped; works regardless of the showMath/showText toggles; alerts
  "nothing to accept/reject on this page" when empty).
  **Note:** `location.reload()` refreshes DOC after OCR job completes (deliberate;
  the old inline `const DOC = ([^;]+);` regex broke on `;` in content).
  Accepted/rejected items are visually distinct: `.item.done` dims, `.item.verified`
  gets a green border/tint, `.item.rejected` a red one (status is also in the meta text).
  Table items can be **consolidated**: the open editor has Merge cells / Split cell
  buttons — **shift+click cells** to toggle them into a selection (highlight), Merge joins
  the selected rectangle into one cell (any N×M; `rowspan`/`colspan`, `.merged` tint), Split
  removes merges on selected cells. **Add row** / **Add column** append an empty row at
  the bottom / column at the right (append-only — existing span coordinates never shift).
  The selection is delegated on the editor container so it survives grid re-renders
  (merge/add re-build the <table>). The span map is
  kept as `item.table_spans` (`[{r,c,rs,cs}]`, 0-indexed over the parsed grid) and
  posted with accept/edit; the exported pipe-markdown keeps the rectangular grid with
  covered cells empty (plain markdown has no span syntax — spans live in `table_spans`).
  The PDF preview (`#pageImg`, left pane) zooms **independently of the validation panel**:
  the fixed-size `#pane` column shrinks the page image to its natural fit width and a zoom
  bar (`−`/`%`/`+`, 25%–400%, `zoomFit` re-fits) + **ctrl+wheel** on the preview set the image
  width in px via `applyZoom()`; `#bboxSel` lives in an `#imgBox` inline-block that
  shrink-wraps the image so the overlay tracks zoom, and `#imgWrap` scrolls when zoomed in
  (zoom re-measured on window resize). `imgFrac()` **clamps to [0,1]** so a draw-box drag
  that ends outside the zoomed image never sends out-of-range fractions to the server.
  Equation items display their markers (`(a) (22.5.1.10a)`) in the item meta line.
  Their Edit + Accept editor adds pre-filled eq-number / eq-letters inputs
  (emptying a field clears that part of the key) so the key is edited in its own
  fields, not derived from the equation text. The draw-box toolbar has a type
  selector (`bboxType`: `auto` default / `equation` / `table` / `text + inline
  math`), forwarded as the `type` field on the `/bbox_ocr` POST (the caption-box
  flow is untouched). Every item has a Delete button — `confirm()` then POST
  `/item/<doc>/<item>/delete`, re-render, no cursor advance; the item and its
  verified/rejected copies are removed.
- `validation/` — `pending/` (docs), `verified/`, `rejected/` (per-item JSON), `uploads/<doc_id>/` (pdf, md, page PNGs).

## Current state (verified facts — don't contradict)

- GLM-OCR runs on **GPU** (CUDA llama.cpp build, `-ngl -1` = auto-offload, not CPU-only).
- Timeouts in `ocr_engine.py` are 600s.
- Itemizer handles **HTML tables** (GLM-OCR sometimes emits HTML instead of markdown).
- Auto-OCR on PDF-only upload works (blue bar, polls, reloads, `?ocr=1`).
- "OCR'd pages only" filter + per-page bulk accept/reject (inline math / text) work via
  the existing `/bulk` route — bulk accept/reject now flip finalized items exactly like
  single-item actions (skip still never touches them).
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
- **Eq-key/section context (resolved):** ACI pages where GLM-OCR bolds
  provision markers (`**22.5.1.2**`) previously stamped `section=None` on every
  item of that page (e.g. the whole of page 404), so equation chunks lacked a
  section anchor and models fell back to recall. Fixed at **parse** (itemizer
  regexes tolerate `**` bold prefixes — future docs) and at **render**
  (rag_uploader backfills the section from the eq key and folds the provision
  statement into equation chunks — applies to the existing verified set, no
  re-OCR: current ACI export backfills 11 eq sections, folds 3 statements).
- Equation keys are edited via dedicated eq-number/eq-letters inputs in the
  Edit + Accept editor and are never auto-re-derived on accept (None = untouched,
  "" = cleared). The draw-box has a type selector (`bboxType`, auto default;
  forced kinds append a single item via `append_bbox_item`; equation draws use
  a dedicated LaTeX-only prompt plus a defensive HTML-table-tag strip). Items can be
  deleted (item + verified/rejected copies removed).

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

- `python3 test_itemizer.py` — 68 itemizer assertions.
- `python3 smoke_test.py` — 111 end-to-end checks via Flask test client (no live OCR;
  asserts `?ocr=1` redirect, no-key OCR error, `parse_page_range`/`parse_page_ranges`
  valid+invalid forms, `ocr_pages` storage incl. multiple ranges, invalid -> 400,
  md-wins-over-range, NaN bbox coords rejected, equation key accept/preserve/clear,
  `append_bbox_item` kinds (incl. HTML-table-wrapper strip for equation/text,
  raw-keep for table), bbox `type` validation, item delete).
- A quick GPU/alive check: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/`
  (app) and `:8888` (unsloth); `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader`
  should show >0% during OCR.