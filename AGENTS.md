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
   markdown → review items), config, schemas (empty), docs.

Related but separate: `~/Projects/StructuralEngineeringWorkspace/` (OpenSeesPy research,
has its own AGENTS.md). This app is the human-review step feeding that research.

## Services / runtime (how it actually runs)

- **Flask app**: `python3 app.py` → `http://127.0.0.1:5000`, single-user local tool.
  Requires `UNSLOTH_API_KEY` env var (no hardcoded fallback). **A hermes-agent
  supervisor respawns the app** — it will auto-restart if killed; use `pkill -f
  "python3 app.py"` and confirm with a fresh `curl`, or restart manually:
  ```bash
  cd ~/Projects/seismic-ai-tools && nohup env UNSLOTH_API_KEY="<key>" python3 app.py > /tmp/seismic-app.log 2>&1 &
  ```
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
Source PDF -> /upload (PDF-only -> auto-OCR via ?ocr=1) or /load (paths to pdf+md)
  -> GLM-OCR (Unsloth :8888) -> markdown -> itemizer.py -> review items
  -> human Accept/Edit/Reject/Skip
  -> validation/verified/ + validation/rejected/ (JSON per item)
```

- `app.py` — Flask routes: `/`, `/load` (paths), `/upload` (multipart, optional md),
  `/ocr/<doc_id>` (async job), `/jobs/<id>` (poll), `/doc/<id>` (review page),
  `/page/<doc_id>/<n>.png` (rendered, cached), `/item/.../action`, `/bulk`.
  Jobs are an **in-memory dict** — lost on restart.
- `config.py` — `API_BASE` (default `http://localhost:8888`), `API_KEY` (env-only),
  `MODEL = "ggml-org/GLM-OCR-GGUF"`, `UPLOAD_DIR`.
- `ocr_engine.py` — `pdf_to_images(dpi=200)`, `ocr_page`/`ocr_batch(workers=2)`,
  `assemble_markdown`. Timeouts were raised to 600s (GLM-OCR f16-on-CPU read timed out
  at 120s; now GPU so fine). Payload is non-streaming chat completions — see note below.
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
  (upload PDF-only → auto-starts OCR). `post()`, `pollJob()` helpers. **Note:**
  `location.reload()` refreshes DOC after OCR job completes (deliberate; the old
  inline `const DOC = ([^;]+);` regex broke on `;` in content).
- `validation/` — `pending/` (docs), `verified/`, `rejected/` (per-item JSON), `uploads/<doc_id>/` (pdf, md, page PNGs).

## Current state (verified facts — don't contradict)

- GLM-OCR runs on **GPU** (CUDA llama.cpp build, `-ngl -1` = auto-offload, not CPU-only).
- Timeouts in `ocr_engine.py` are 600s.
- Itemizer handles **HTML tables** (GLM-OCR sometimes emits HTML instead of markdown).
- Auto-OCR on PDF-only upload works (blue bar, polls, reloads, `?ocr=1`).
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
- Do not paste the API key into code; it lives in env (and in the hermes supervisor's
  launch command line).
- Do not fabricate results; verify against actual tool output (tests, curl, logs).

## Tests / verification

- `python3 test_itemizer.py` — 61 itemizer assertions.
- `python3 smoke_test.py` — 46 end-to-end checks via Flask test client (no live OCR;
  asserts `?ocr=1` redirect, no-key OCR error, etc.).
- A quick GPU/alive check: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/`
  (app) and `:8888` (unsloth); `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader`
  should show >0% during OCR.