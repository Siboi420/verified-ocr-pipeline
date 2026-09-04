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

## ⛔ Mandatory: keep README.md in sync with meaningful changes

**Every meaningful change — new features, UI/UX changes, behavior or
route changes a user would notice, test-count updates, layout/setup
changes — MUST also update README.md in the same commit.** README.md is
 the human-facing entry point (what the app does, how to run it);
 AGENTS.md is the AI-facing technical reference. Both must tell the same
 story. Trivial diffs (typos, internal refactors with no visible effect,
 comment-only changes) can skip it — "meaningful" means visible to a
 user or a reader of the README.

 Before committing, ask: does this diff change something README.md
 describes? If yes, update the README bullets in the same commit: feature
 lists, screenshots/blurbs, test counts, run instructions. A commit that
 adds a user-visible feature but not the README line is incomplete.
 (This rule is the result of the dark-mode + chat-layout work landing
 without README updates.)

## What this project is

A local OCR-validation toolkit for structural/seismic engineering research (Kresna's
MEXT/SSI-lateral research). **Source PDF → GLM-OCR → human review → verified data.**

Two things live here:

1. **One server** — Flask web UI (`localhost:5000`) serving: OCR validation
   (item-by-item review: tables, equations, text → verified JSON), a **chat**
   UI with multi-turn savable sessions + a developer trace (retrieval /
   reasoning / tool calls), **Unsloth KB management** (list/add/rename/delete
   + upload verified items → KB), and **model management** (load GLM-OCR or
   granite; unload-before-load swaps, one resident model at a time). Browser-
   style tabs: `[OCR Validation] [Chat]`.
2. **Supporting tooling** — `ocr_engine.py` (GLM-OCR client), `itemizer.py` (OCR
   markdown → review items), `rag_uploader.py` (verified JSON → Unsloth RAG KB),
   `models.py` (Unsloth model load/unload/status), `orchestrator.py` (CLI-only
   RAG + tool-calling engine the chat route imports; no HTTP server of its own),
   config, schemas (empty), docs (`docs/rag-query-guidance.md` = how to query the
   KB for table/formula questions; `docs/infrastructure.md`, `docs/corrections.md`,
   `docs/ocr-fix-context.md`, `docs/voice-roadmap.md` = voice/AIRI roadmap, docs-only).

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
- **One server, one port**: the Flask app on `:5000` is the only server. The old
  stdlib orchestrator server on `:5001` is gone (`--serve`/`--port` removed —
  `orchestrator.py` is CLI + importable engine only; the chat UI lives in Flask).
- **Unsloth Studio**: `http://127.0.0.1:8888`, serves the GLM-OCR model via OpenAI-style
  `/v1/chat/completions`. Auth via `Authorization: Bearer <UNSLOTH_API_KEY>`.
  - OCR model: `ggml-org/GLM-OCR-GGUF` (Q8_0 as of last swap). Loaded via:
    `POST /api/inference/load {"model_path":"ggml-org/GLM-OCR-GGUF","force_reload":true}`
  - **Chat model**: `ibm-granite/granite-4.2-8b-GGUF` (`config.CHAT_MODEL`; the
    old Qwen 3.8 / granite-4.1 constants are gone). GGUF download/reload is the
    user's step — our code only
    calls load/unload. Loaded at `context_length 32768` (`config.CHAT_MAX_SEQ_LENGTH`;
    Qwen needed that override — backend default was 17408). The chat engine caps
    `max_tokens` at 12000.
  - **Model unloads** on `POST /api/inference/unload` — the body REQUIRES `model_path`
    (verified 2026-09-03: `{}` → 422 "Field required"); a path that isn't loaded is a
    harmless no-op (`{"status":"unloaded"}`). `{"force_cancel_active":true}` kills
    stuck non-cancellable generations (ocr_engine sends non-streaming calls). After
    unload, OCR errors with "No model loaded" until reloaded.
  - **Model status**: `GET /api/inference/status` → `active_model` / `loaded[]` /
    `loading[]`; `active_model` may report a RESOLVED LOCAL SNAPSHOT path rather than
    the hub id (`models` matches by filename suffix). `POST /api/inference/load` may
    return before the model is ready — `models.load()` polls status (~2s) up to 120s.
  - **Backend is CUDA now**: `POST /api/llama/backend {"backend":"cuda"}` switches the
    llama.cpp build (WSL2 RTX 2080 Ti 11GB, `/dev/dxg`). GPU offload verified
    (100% util, ~2.6s/inference vs 10-15s CPU).
  - Only one model loads at a time — swaps are unload-before-load, driven by the
    per-page **model bar** (`POST /api/model/load` → background `/jobs/<id>` poll with
    `step` toast: `unloading <name>` → `loading <name>` → `loaded <name>`; "already
    loaded" short-circuits with no GPU churn). Verified live 2026-09-03: GLM-OCR
    `loading` → `done/loaded`.
  - **KB CRUD**: list `GET /api/rag/knowledge-bases` (`{knowledgeBases:[{id,name,
    description,documentCount}]}`); create `POST` (same path, `{name, description?}`);
    rename is **`PATCH /api/rag/knowledge-bases/{kb_id}`** `{name}` (verified — no
    create+reupload fallback needed); delete `DELETE /api/rag/knowledge-bases/{kb_id}`.
    Upload a doc: `POST /api/rag/knowledge-bases/{kb_id}/documents` (multipart).
- **WSL2, RTX 2080 Ti 11GB**, Python 3, Flask, PyMuPDF. Deps in `requirements-ocr.txt`.
- **Querying the KB**: `POST /api/rag/search` defaults to `mode:"hybrid"` (BM25+dense),
  `top_k` 1-50 (default 10), `min_score` filters the dense component (default 0.0);
  the chat path declares a `search_knowledge_base` tool + `enable_tools: true` and
  passes `rag_scope` (`kb_id`, `mode`, `default_top_k`, `autoinject`) — bare
  `rag_scope` without the tool injects NOTHING (verified: prompt stayed 117 tokens
  and the model hallucinated a formula). For table/formula questions: hybrid +
  `top_k: 10` + branch-first prompt instruction + provenance headers. Recipes and
  verified shapes: `docs/rag-query-guidance.md`.

## Architecture / data flow

```
Source PDF -> /upload (PDF-only -> auto-OCR via ?ocr=1; optional ocr_pages="2-3, 4-9"
  page ranges for the initial OCR run) or /load (paths to pdf+md)
  -> GLM-OCR (Unsloth :8888) -> markdown -> itemizer.py -> review items
  -> human Accept/Edit/Reject/Skip
  -> validation/verified/<doc_id>/ + validation/rejected/<doc_id>/ (JSON per item)
```

- `app.py` — Flask routes: `/`, `/load` (paths), `/upload` (multipart, optional md,
  optional `ocr_pages` form field), `/ocr/<doc_id>` (async job), `/jobs/<id>` (poll,
  live `done`/`total` page counts), `/doc/<id>` (review page),
  `/page/<doc_id>/<n>.png` (rendered, cached), `/item/.../action`, `/item/.../delete`,
  `/item/.../order` (move item to a 1-based page position `{order: <finite number>}`; the page renumbers to a contiguous 1..n — non-finite/missing -> 400),
  `/bulk`.
  Jobs are an **in-memory dict** — lost on restart. `upload()` validates `ocr_pages`
  (`N`, `N-M`, or comma-separated like `2-3, 4-9`, 1-indexed; invalid -> 400) and
  stores `doc["ocr_pages"]` as a list of `[start, end]` pairs (e.g. `[[2,3],[4,9]]`;
  legacy flat `[5,10]` still accepted) only in the no-markdown branch (md uploads
  ignore the field). `_run_ocr(page_range=None)` passes the stored value straight to
  `pdf_to_images`
  (clamps, merges), errors clearly if zero pages match, and pushes `done`/`total`
  into the job dict via `ocr_batch(on_progress=...)`. `/ocr/<doc_id>` on a doc with
  items is **incremental OCR**: it requires `ocr_pages` in the JSON body (form field
  fallback; same grammar as upload; invalid -> 400) and behaves as "skip, never
  replace" — wanted pages are expanded (clamped to `n_pages`), already-covered
  pages (`pages` with items) are subtracted, only genuinely new pages run through
  `_run_ocr(page_range=new_pages)` which **merges** the fresh blocks into the
  existing `--- Page N ---` markdown via `merge_ocr_markdown` (leading `# OCR:`
  header kept, page blocks updated by number, reassembled ascending; a doc with no
  existing md content falls back to a fresh full `assemble_markdown`). The merge
  persists via `merge_pages_into_doc`, which **preserves review state**: items are
  re-parsed but restored by their deterministic id (`<doc_id>-p<n>-i<k>`) —
  status, edited content, `table_spans`, eq keys — and drawn-box (`-bbox*`)
  items, which live only in the page dicts and not the md, are re-attached
  (previously a wholesale re-parse dropped them). Response is
  `{job_id, skipped}` (skipped = wanted-count minus new). Fully-covered or empty-
  after-clamp ranges -> 400 ("all requested pages already OCR'd") with no job
  started; no range at all -> 400 naming `ocr_pages`. Covered pages are never
  re-OCR'd, so verified/rejected items are safe. bbox/caption routes parse box
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
  `apply_action` copies the item's `order` into the verified/rejected JSON
  payloads, so KB exports follow it.
  `doc_view` calls `_ensure_order(doc)` once per doc (persisted
  `_order_stamped` marker): legacy docs get document order — re-parse the
  md, stamp `order` by id onto existing items (bbox/drawn items tail the
  page after the highest stamped order, in append order; deleted items are
  not resurrected) — without touching review state.
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
  **Chat/KB/model routes (one-server merge 2026-09-03):** `GET /chat` renders
  `templates/chat.html` (session loaded from `?s=<sid>`, else null). Chat sessions
  are one JSON file per session under `BASE/sessions/` (gitignored; routes read the
  `SESSIONS_DIR` module global, so smoketest overrides it): `{id, name, kb_id|null,
  created_at, updated_at, messages:[{role, content, ts}]}`. `GET/POST
  /api/chat/sessions` (create requires non-empty `name` → 400; 201 with the
  session), `GET/PATCH/DELETE /api/chat/sessions/<sid>` (sid regex-validated;
  PATCH accepts `name` and/or `kb_id` — `null` clears; malformed sid → 404),
  `POST /api/chat/sessions/<sid>/messages` `{content, developer?, kb_id?}` →
  appends the user turn, runs `orchestrator.answer_turn(content, history, kb_id)`
  (no positional max-tokens — the resolved generation profile supplies it),
  persists both turns, returns `{answer, session, trace?}` — the trace key is
  present only when `developer` is truthy (live-only, never persisted); a failed
  turn pops the user message and returns 502, keeping the session retryable.
  The POST mirrors the PATCH `kb_id` semantics: a `kb_id` field in the body
  overwrites the session's stored value (the send carries the dropdown, so the
  turn uses exactly the KB shown — no change/send race); omitted → the stored
  value. Each persisted assistant message carries the KB tag it was answered
  from: `kb_id` + `kb_name` (resolved at answer time via the `_kb_label` helper
  → `rag.list_kbs()` display name, raw id fallback when the backend is
  unreachable; `kb_id == null` → both `None`, "no KB").
  `kb_id == null` skips retrieval (bare tools only).
  Generation profiles: `GET /api/settings` → the stored `settings.json`
  (`{"global": {}, "models": {}}` when missing/corrupt, never a 500); `POST
  /api/settings` sanitizes + persists via `profiles.save_settings` and returns
  the clean shape (bad values — NaN/Inf/garbage/non-object sections → 400).
  `MAX_CHAT_TOKENS` is gone (moved into `profiles.DEFAULTS`).
  KB routes: `GET /api/kb` (list via `rag.list_kbs`, 502 on backend RuntimeError),
  `POST /api/kb` (create, 400 empty name), `PATCH/DELETE /api/kb/<kb_id>`
  (rename/delete; id regex-validated), `POST /api/kb/<kb_id>/upload` `{doc_id}`
  or `{"doc_id":"__all__"}` → `{uploaded:[filenames], skipped:n}` (single doc
  404s when it has no verified items; `__all__` uploads every verified doc and
  counts doc dirs that yielded nothing).
  Model: `GET /api/model` → `{loaded, available, job}` — `loaded` is the raw
  resident path (string or null); `available` is `[{path, name, variant?}]` —
  one entry PER INSTALLED QUANT of each GGUF model actually cached on disk,
  via `models.list_models()` (`GET
  /api/models/cached-gguf` — the real disk cache, NOT the `/api/models/local`
  registry which also lists empty/half-installed repos with no weights; the
  quant is parsed from each snapshot's GGUF filename, `mmproj-*` vision
  projectors skipped) — a listing failure
  degrades to `[]`, never a 502 — the header must not die over a listing
  error); the `key` field is gone. `job` = `{id, status, step}` for an
  in-flight model swap, else null — the `MODEL_JOB_ID` module global points at
  the active job and the worker clears it on done/error, so the header
  re-attaches progress after a tab switch/reload; tabs are full page loads.
  `POST /api/model/load` `{model:"<path>", variant?}` (non-empty literal
  path; empty ->
  400; optional `variant` = the per-quant dropdown selection → threaded into
  `models.load(path, variant)`) → `{status:"done", step:"already loaded"}`
  when the requested GGUF
  filename is a suffix of the resident path or the variant appears in it (no
  GPU churn), else a background
  job in the shared `JOBS` dict (`unloading <label>` → `loading <label>` →
  `done/loaded <label>` or `error`, labels via `_model_label`: available-list
  display name if known, else `Path(path).name`), polled via `/jobs/<id>`. The
  worker ALWAYS calls `models.unload(current_path)` (raw loaded path; no-op
  when nothing loaded) before `models.load(path, variant)` — unload-before-load is the
  single-model invariant. The worker also resolves the target's profile
  `context_length` and passes it as `max_seq_length` to `models.load`
  (`None` = keep the per-role config default — chat 32768 / OCR None).
  Verified live 2026-09-03 against :8888.
- `rag_uploader.py` — reads `validation/verified/<doc_id>/*.json` (one folder per
  doc), groups items by
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
  statements stay unfolded — current ACI export: 44 eq chunks, 31 folded).
  Table chunks get the same treatment: caption stays the plain-words surface
  (the Table 22.5.5.1 retrieval miss was an uncaptioned table chunk), section
  backfilled from `table_number`, AND the rows are rendered as a SINGLE
  **canonical clean-Unicode table** (each cell through `_math_to_text` → `λ_s`,
  `ρ_w`, `√(f_c′)`, `β`, fractions `(N_u)/(6A_g)`). A table chunk emits exactly
  ONE representation — caption + this normalized table (the raw pipe/LaTeX
  mirror is dropped so the model never reconciles two copies of the same
  table) — plus a `Symbols:` line inlining local definitions for whichever of
  `A_g`/`b_w`/`b_o`/`N_u`/`β`/`α_s` that table actually uses, so the model
  stops hedging on symbols defined elsewhere. (12 table chunks, all
  normalized + annotated.) Per-page ordering groups
  code + R-commentary + subsections: items sort by a numeric section tuple
  (R directly beneath its code, then subsections), fragments with no section
  inherit the nearest sectioned same-page predecessor, else the previous
  page's last section (continuation fragments like "where …"/"Notes: …"
  across page boundaries), and true orphans sort to the page tail; bold-marker
  statement titles also get their number via `_stmt_key`. Items
  order by `order` when present (integer 1-based position),
  else by the numeric index of `item_id` (lexical sort put i10
  before i2)
  and uploads to an Unsloth Studio RAG KB (`/api/rag/knowledge-bases`, name via
  `--kb`, default "Verified OCR"). Server-side chunking + embeddings. Skips
  unreadable JSON **and stale pre-metadata exports** (missing `source_name` — the
  old bbox-OCR artifacts) with warnings. `--selftest` for offline checks,
  `--dry-run` to render without uploading. Requires `UNSLOTH_API_KEY`.
  **App-facing helpers (2026-09-03):** `_api()` raises `RuntimeError` (not
  SystemExit) so the Flask routes can map failures to JSON 502 — `main()`
  catches it back to `SystemExit` for the CLI. Added `list_kbs()`, `create_kb(
  name, description=None)`, `rename_kb(kb_id, name)` (posts `PATCH
  /api/rag/knowledge-bases/{kb_id}` `{name}` — verified the verb, no
  create+reupload fallback needed), `delete_kb(kb_id)` (DELETE), and
  `docs_for_doc(doc_id)` (filter over `docs_from_verified` for single-doc
  uploads); `get_or_create_kb` now reuses `list_kbs`/`create_kb`.
  **Table math → clean Unicode (λ-drop fixed):** `_math_to_text` decodes
  `\lambda_s`→`λ_s`, `\rho_w`→`ρ_w`, `\sqrt{…}`→`√(…)`, `\prime`→`′` and KEEPS
  `_` on subscripts (both `\lambda_s` and `\lambda_{s}` → `λ_s`, `\rho_w` and
  `\rho_{w}` → `ρ_w`), so the two λ factors in Table 22.5.5.1 row (c) and
  Table 22.6.5.2 stay distinct — previously `_` was stripped and both merged
  into unsegmentable `lambdas`, making qwen drop one λ under recall (their
  `0.33λ_sλ√(f_c′)` rendered as `0.33lambdaslambdasqrtfc'`). `_selftest`
  guards both the unbracketed and braced-subscript (two-way) paths.
  Deliberately deferred: preserving `^` or adding a `*` separator (re-add
  only if the re-test still drops a λ).
- `profiles.py` — generation settings store (stdlib only): one **global**
  default profile plus **per-model** overrides, persisted in `BASE/settings.json`
  (gitignored, `SETTINGS_PATH` module global so tests redirect it).
  `DEFAULTS` = built-ins: `temperature 0.2`, `repeat_penalty 1.1`,
  `max_tokens 12000`; `top_k`/`top_p`/`min_p`/`context_length` default `None`
  (unset = inherit; per-request params resolving to None are OMITTED from the
  `/v1/chat/completions` payload so sampling params stay off unless set;
  `context_length` is **load-time only** — the model worker threads it into
  `models.load(max_seq_length=…)` and it applies on the next load, never a
  request param). Resolution order: `DEFAULTS <- global <- models[key]`.
  `load_settings()` returns `{}` on missing/corrupt file (fall back to
  defaults, never raise); `save_settings()` = sanitize + persist; `sanitize()`
  coerces numeric strings, drops unknown keys, treats `''`/null as unset
  (dropped), rejects NaN/Inf/garbage with `ValueError` (→ 400 via the routes),
  and drops empty per-model entries (that's the "clear override" action).
  Model matching: exact key, or a key's base name (trailing `-GGUF` stripped)
  inside the model path — a resolved snapshot path like
  `granite-4.2-8b-Q6_K.gguf` matches the repo-id key
  `ibm-granite/granite-4.2-8b-GGUF` (same suffix approach as the model bar).
  `--selftest` covers defaults/global/per-model override + snapshot-path
  matching + sanitize (incl. NaN/Inf/garbage rejection). Chat generation
  params only; RAG retrieval `top_k=3` and oCR sampling are untouched.
- `models.py` — model management for Unsloth (stdlib urllib, `_api` raises
  RuntimeError → Flask maps to JSON 502; `--selftest` offline). `MODELS =
  {"ocr": config.MODEL, "chat": config.CHAT_MODEL}` is the default-role mapping,
  kept for `_selftest` and config defaults (`load()` also accepts literal
  paths); `current_model()` = `GET /api/inference/status` → `active_model` or
  first of `loaded[]` (None when nothing resident); `list_models()` = `GET
  /api/models/cached-gguf` → `[{path, name, variant?}]`: the real disk cache
  is the source of truth (the `/api/models/local` registry lists empty/
  half-installed repos with no weights) and each cached repo contributes ONE
  entry per installed quant, parsed from the snapshot GGUF filenames
  (`granite-4.2-8b-Q6_K.gguf` → `granite-4.2-8b (Q6_K)`;
  `mmproj-*` vision-projector files skipped), so several quants of one model
  are separately selectable; `unload(model_path, force=True)` posts to
  `/api/inference/unload` with `force_cancel_active:true` (kills
  non-cancellable in-flight generations; unknown/None path is a harmless no-op
  — verified live); `load(model, variant=None, max_seq_length=None)` accepts a
  `MODELS` key
  ("ocr"/"chat" -> its config path) or a literal path, posts `{model_path,
  force_reload:true, gguf_variant?, max_seq_length?}` — `variant` pins the
  quant via `gguf_variant` (falls back to `config.CHAT_GGUF_VARIANT` for the
  chat default), and `max_seq_length` comes from an explicit arg (the
  profile's `context_length`, threaded by the app worker) or, when `None`,
  the per-role config default: sent ONLY when the path equals the
  chat/ocr config default; other paths get the backend default — then polls
  status (~2s, up to 120s) until the target reports loaded — the backend may
  resolve a hub id to a local snapshot path, so readiness matches by GGUF
  filename suffix.
  **GGUF-variant foot-gun (granite-4.1, verbatim from the live 2026-09-03
  incident):** on the OLD `unsloth/granite-4.1-8b-GGUF` the backend's default
  variant was `UD-Q4_K_XL`, which was NOT
  cached — loading the bare repo id triggered a multi-GB HTTP re-download
  (xet transport stalls, then a forced clean re-download; the model stays
  `partial: true` and slow), which is the "chat takes way too long" symptom.
  The cached quant was `UD-Q6_K_XL` (7.9 GB, full snapshot under
  `~/.cache/huggingface/hub/models--unsloth--granite-4.1-8b-GGUF/`). The
  variant is passed ONLY via the `gguf_variant` field of `POST
  /api/inference/load` — the backend REJECTS a `:UD-Q6_K_XL` suffix inside
  `model_path` (it treats the whole string as a literal repo id → 500 "Repo
  id must use alphanumeric chars"). With the variant pinned, the load
  resolves straight to the cached file (no download) and the status reports
  `display_name "granite-4.1-8b-GGUF (UD-Q6_K_XL)"`; `/api/models/local`
  then shows granite as `partial: false`. The stale `*.incomplete` Q4 blob
  (hundreds of MB) is harmless; the backend deletes it lazily.
  **granite-4.2 now (verified on disk + resident):** `config.CHAT_MODEL` =
  `ibm-granite/granite-4.2-8b-GGUF`, pinned to its single cached quant
  `Q6_K` (`granite-4.2-8b-Q6_K.gguf`, full snapshot under
  `~/.cache/huggingface/hub/models--ibm-granite--granite-4.2-8b-GGUF/`) via
  `config.CHAT_GGUF_VARIANT = "Q6_K"` — same rule applies, the suffix goes in
  `gguf_variant`, never `model_path`.
  No CLI action besides `--selftest`.
- `orchestrator.py` — RAG + tool-calling Q&A **engine + CLI only, no HTTP server**
  (stdlib + urllib, no deps). Question from `--question` or stdin (both empty →
  usage, exit 2; missing `UNSLOTH_API_KEY` → error, exit 1; `--max-tokens` default
  12000 — capped so a reasoning model can't burn the whole window in COT and stop
  empty; the system prompt also tells it to answer directly without a reasoning
  preamble and, since 2026-10-08, to call a tool only when a number needs
  computing and to stop + answer immediately once all tool results are back
  (no further tool calls, no re-runs, no repetition). `run_loop(messages, tools, max_tokens, thinking=False)` →
  `(answer, trace)` (trace =
  ordered `{"kind":...}` steps: `reasoning` whenever the backend emits
  `reasoning_content`, `message`, `tool_call` (name + raw args), `tool_result`
  (wrapper result or `{error}`), final `answer`); `answer_turn(user_turn, history,
  kb_id, max_tokens=None)` → `(answer, trace)` with a `retrieval` step prepended when
  `kb_id` is set (`None` → no retrieval; `max_tokens=None` → the resolved
  profile's cap, applied in `chat()`); **no-KB sessions answer from general
  knowledge (2026-11):** when `kb_id` is None the context is
  `"(no knowledge base attached)"` and the system prompt swaps to
  `SYSTEM_PROMPT_BARE` (drops the RAG source-citation / "if not in your
  sources, say so" guardrails; tool/format rules identical) — previously
  the model refused general questions like "what is concrete made out of?"
  with "not in your sources" when no KB was attached (session
  a192daf89093). With `kb_id` set the full `SYSTEM_PROMPT` (source
  guardrails) applies. `chat(messages, tools, max_tokens=None,
  thinking=False)`
  resolves the loaded model's generation profile once via
  `profiles.resolve(_loaded_model())` and sends `temperature`, `repeat_penalty`,
  any set `top_k`/`top_p`/`min_p` (None params omitted from the payload),
  **and `enable_thinking: <thinking>` EXPLICITLY on every request** — the GGUF
  backend loads with thinking on (Studio-managed `chat_template_kwargs`), so an
  explicit per-query False is the only way to run the fast path; `answer_turn`
  routes per query: ambiguous/judgment phrasing (`should`/`recommend`/
  `dimension`/`assume`/`if`/`or` keywords — `_AMBIGUOUS_RE`) → first pass with
  `thinking=True`; straightforward parameter extraction (e.g. the Av,min
  question, no keyword) → first pass `thinking=False`; a failed fast pass
  escalates to `thinking=True` ONCE (`_wants_retry`: empty answer, a tool-call
  error surfaced to the model, or a calc-style question — `capacity`/`shear`/
  `moment`/`flex`/`Ast`/`Av`/`kN`… — answered without any tool call); the retry
  restarts from the seed messages (fresh `_question_messages`), retrieval runs
  once; the forwarding keyword heuristic is marked `ponytail:` (upgrade path:
  a real classifier or retry-only policy if it misfires);
  the `TEMPERATURE` constant is gone (moved into `profiles.DEFAULTS`);
  `answer_question` (CLI) resolves
  `DEFAULT_KB_NAME = "Verified OCR"` by NAME at runtime (`default_kb_id()`, creates
  if missing) so rename/delete of KBs never strands the CLI on a stale id.
  Retrieves `top_k=3` hybrid chunks via `POST /api/rag/search` (`text` field),
  globs `schemas/*.json` into OpenAI function tools, loops (≤8 iters) against
  `config.CHAT_MODEL` (`ibm-granite/granite-4.2-8b-GGUF`): tool calls run through
  `functions/wrapper.py:call_tool()`; wrapper `ValueError`s are serialized
  `{"error": …}` back to the model, never crash the loop; final non-tool message
  is the answer, exit 0; iteration cap → exit 1. Tool-call parsing handles both
  the native `message.tool_calls` shape and a `<tool_call>`-marker fallback
  (native-only re-verified live on granite-4.2 2026-09-26: the backend
  converts its chat-template `<tool_call><function=…><parameter=…>` XML into
  native `message.tool_calls`, so no XML-parsing branch is needed). **Repetition guard (2026-09-03):** the
  final answer passes through `_truncate_repetition` — a run of ≥5 identical
  non-trivial lines (≥40 chars) is cut at the first repeat and replaced with a
  truncation note, so the 8B-model degenerate-loop failure (seen in session
  c1f3db7e07a0: 343 identical `≈ 0.75 × 0.17 × 5.477 × 1{,}030{,}400` lines
  burning the whole token budget) never reaches the UI/session. **Repetition fix
  (2026-09-03):** the chat payload carries `repeat_penalty: 1.1` (verified
  accepted live against :8888 — HTTP 200, no unknown-param rejection; the
  fallback would have been `frequency_penalty: 0.5`), and the system prompt
  gained two routing lines: re-run the tool with the same inputs when the user
  asks to break down/explain/verify a previous tool result (never recompute from
  memory), and "Ast" / "minimum shear reinforcement" / "minimum stirrup area"
  all mean Av,min → answer with the `min_shear_reinf` tool. `_truncate_repetition`
  stays as the safety net. System prompt is the
  `docs/infrastructure.md` guardrails (no arithmetic from memory, cite sources,
  flag uncertainty) plus a b×h note (sections like "200x300" are b×h — capacity
  tools take d, or h with `cover_cg`, never total height h as d). `--selftest` is
  offline: tool-load, native + marker extraction, `_norm_args`, round-trip
  shapes, a `run_loop` trace-shape assert with `chat()` stubbed, and a chat
  payload-shape assert (stubbed `_api` → `repeat_penalty == 1.1`).
- `config.py` — `API_BASE` (default `http://localhost:8888`), `API_KEY` (env-only),
  `MODEL = "ggml-org/GLM-OCR-GGUF"` (OCR), `CHAT_MODEL =
  "ibm-granite/granite-4.2-8b-GGUF"` (chat), `CHAT_GGUF_VARIANT =
  "Q6_K"` (pins the single cached quant — granite-4.2's
  only installed quant; the suffix goes in the load's `gguf_variant` field,
  never `model_path` — see the models-matching
  foot-gun under `models.py` for how the old granite-4.1
  `UD-Q4_K_XL` default stalled on download), `CHAT_MAX_SEQ_LENGTH = 32768` (explicit
  context-length override, same as Qwen needed; None → omit the load field),
  `OCR_MAX_SEQ_LENGTH = None` (backend default), `UPLOAD_DIR`.
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
  `(\(...\)` / `$...$`). Item order is **document order**: every item
  carries `order` (1-based document position, stamped BEFORE the priority
  sort — `_parse_page_body`'s kind/line sequence), and display/export sort
  by `order`; the type-priority sort (equation → table → text-math → text)
  survives only as the invisible deterministic id-assignment mechanism so
  ids stay stable across merges/status restores. `order` is an int (1-based;
  the reorder route renumbers the whole page to 1..n on manual edits).
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
- `templates/_header.html` — shared top bar included by both pages (Jinja
  includes inherit the render context, so `tab` arrives from the route):
  browser-style tab strip (`[OCR Validation] [Chat]`, active tab via the `tab`
  context var), the model bar (status chip polling `GET /api/model` + a single
  `#modelSel` dropdown of the installed models — `modelState()` fills it from
  `j.available` (option value = path, text = name, `data-variant` = the quant
  when present) and selects the loaded path
  by GGUF-name suffix — plus a `#loadBtn` **Load** button posting the selected
  path (and its `variant`) to
  `/api/model/load`; both disabled while a swap job runs), a fixed
  toast container, and shared JS: `toast()`, `progress()`/`clearProgress()`, a
  swap shows one live toast (`unloading <name>` → `loading <name>` →
  `✓ loaded <name>`, red toast on error),
  `pollModelJob(jobId)` (polls `/jobs/<id>`; shared by load and by re-attach),
  `loadModel()` (posts the dropdown's selected path to `/api/model/load`,
  delegates to `pollModelJob`),
  `modelState()` (chip on load; if `/api/model` reports a running `job`, shows the
  progress toast and calls `pollModelJob` — this is how progress survives a tab
  switch, since tabs are full page loads), and `window.kbFetch()` (GET /api/kb
  helper).
  **Dark theme + shared toggle (2026-10-10):** this template is the single
  source of truth for colors — the full token set (`--bg`/`--surface`/
  `--surface-hover`/`--text`/`--muted`/`--line`/`--line-strong`/`--accent`/
  `--accent-bg`/`--accent-text`/`--ok*`/`--err*`/`--warn*`/`--purple*`/
  `--gray*`/`--msg-assistant-bg`/`--code-bg`/`--merged-bg`/`--ocrbar*`) is
  defined here as `:root { … }` (light) + `:root[data-theme="dark"] { … }`
  (dark, default) with matching `color-scheme`, plus the shared
  `input, select, textarea { background: var(--surface); color: var(--text); }`
  rule so form fields follow the theme. The two pages removed their own
  `:root { --line … }` mini-blocks (consolidated). `#themeBtn` (☾ in dark /
  ☀ in light, right of `#loadBtn`) flips `data-theme` on `<html>` and writes
  `localStorage.theme`; the pages hard-code `data-theme="dark"` on `<html>`
  and an inline head script overrides it with the stored choice before first
  paint (anti-FOUC). The PDF preview (`#pageImg`) intentionally stays white in
  both themes; `.trace`/`.toast` are already dark and stay hardcoded.
- `templates/index.html` — single-page JS UI. Includes `_header.html` (`
  tab="ocr"`, `page_model="ocr"`; the old inline `<header>` is now a card row
  holding the doc pill / counter / OCR-coverage / OCR-more inputs). List branch
  gained a **Knowledge Bases card** (list / create / rename via prompt /
  delete via confirm, backed by `/api/kb*` via `window.kbRefresh()`); the doc
  branch gained an **Upload verified → KB** control (`#upKbSel` + `#upKbBtn`,
  posts `/api/kb/<id>/upload {doc_id}` and shows the result in `#upKbMsg`).
  Has an `autoOcr()` + `?ocr=1` gate
  (upload PDF-only → auto-starts OCR) and an `ocrMore()` + `#ocrMorePages`
  input/button on the doc page ("OCR more pages": posts `{ocr_pages}` to
  `/ocr/<doc_id>`, shows `skipped` already-OCR'd pages, reuses `#ocrbar` + `pollJob`,
  `location.reload()` on done). Upload form has an optional `ocr_pages` text
  input. `post()`, `pollJob(jobId, done, progress)` helpers (progress callback
  updates the line while running). Auto-OCR bar and upload status show live
  `D/T pages`. Header shows an "OCR'd X/Y pages" coverage line (pages with ≥1 item
  over `n_pages`), set by `render()`, which is now also called once on doc-page
  load (the page selector/items previously only rendered after a user interaction).
  The item counter always reflects the current page — pages with no items (e.g.
  outside the OCR page range) show "no items on page N", never a stale count from
  a previously-viewed page.
  Server page items arrive in priority (id-assignment) order; `render()` calls
  `sortItems()` first, which re-sorts each page by `order` (missing order →
  Infinity, so legacy items keep their array order after ordered ones — stable
  sort). Each item gets a small editable **order number** input (`.order`, in
  the action row; `type=number`, min 1, max = the page's item count); a
  `change` posts `{order}` to `/item/<doc_id>/<item_id>/order` and re-renders:
  the server moves the item to that 1-based position and renumbers the whole
  page 1..n (other pages untouched).
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
  **Theming (2026-10-10):** every hardcoded color here was replaced with the
  shared tokens from `_header.html` (`body` → `--text`, `.card`/`.item` →
  `--surface`, badges → `--ok-bg`/`--warn-bg`/`--purple-bg`/`--gray-bg`
  families, table grids → `--line-strong`, merged cells → `--merged-bg`,
  `#ocrbar` → `--ocrbar-bg`/`--ocrbar-border`, `.ok`/`.err` → `--ok`/`--err`,
  `.item.verified/rejected/active` → `--ok-border`/`--err-border`/`--accent`);
  `#pageImg` keeps `background:#fff`, `#bboxSel` keeps its translucent red
  overlay (drawn on the white page). The local `:root { --line … }` block is
  gone; `<html>` carries `data-theme="dark"` + the anti-FOUC head script.
  No route/API change — the smoke-tested DOM ids are untouched.
- `templates/chat.html` — chat page (`tab="chat"`; loads a session from
  `?s=<sid>`, `const SESSION` is `null` when none — routes guard that).
  Sidebar: sessions list (click to switch) + new / rename / delete;
  lazy-creates a session on first send. Thread: user right / assistant left,
  **markdown via marked@15 CDN** (tables/bold/headers/lists, `breaks:true`)
  **+ LaTeX via MathJax**, rendered with `renderMarkdown()`: escape `&`/`<`,
  swap whole math spans (`\(…\)`/`\[…\]`/`$$…$$`/`$…$`) for NUL placeholders so
  marked's inline escape rule can't strip the `\(`/`\[` backslashes or mangle
  `_`/`^` inside math, `marked.parse`, restore placeholders, then
  `MathJax.typesetPromise([el])`. Restore uses a **replacer function**: a
  string replacement would interpret `$` in the math — display `$$…$$` spans
  collapse to `$` and `$&`/`$\``/`$'`/`$1` mangle (verified 2026-09-03). DOM
  insertion via `createContextualFragment`
  + `replaceChildren` (no `innerHTML`), plus a link-scheme guard (non-http(s)/
  relative href|src -> dead `#`). Math spans get one content fix: the granite
  chat model sometimes emits `\Granite` where it means the strength-reduction
  factor `\Phi` (its own name leaks into the math it echoes from tool output —
  seen in session 86c6c4b58ce3, `\(\Granite V_n\)` = φV_n, `\Granite = 0.75`
  per §21.2.1), so `\Granite` is rewritten to `\Phi` inside math spans at
  extraction time (`/\\granite/gi`). Same rewrite slot carries the **math
  parameter-name fix**: the model emits `\text{cover\_cg}` (e.g. session
  09db31f8448d), which MathJax renders as the literal string "cover_cg" — a
  backslash-underscore inside `\text{}` is NOT a subscript (verified live
  in headless Chromium) — so every spelling (`\text{cover\_cg}`,
  `\text{cover}_{cg}`, plain `\text{cover}`) is rewritten to the single
  letter `p` (the user's notation, no `cg` subscript;
  `\text{cover}_{cg}` consumes the trailing `_{cg}` so it can't survive as
  `p_{cg}`), and the sibling underscore names render as spaced text —
  `\text{clear\_cover}` → `clear cover`, `\text{stirrup\_diameter}` →
  `stirrup diameter`, `\text{longitudinal\_bar\_diameter}` → `longitudinal
  bar diameter` — so the equation `p = clear cover + stirrup diameter +
  (longitudinal bar diameter)/2` reads as words, never as
  `clear\_cover+stirrup\_diameter` (phrases like `\text{minimum cover}`
  never match the exact `\text{cover` prefix). `.msg` keeps `overflow-wrap` instead of
  `pre-wrap` (`breaks:true` supplies line breaks); minimal table CSS added;
  error/toast paths and the dev trace stay plain text. KB selector with a "no
  KB" option — the message POST carries the dropdown's `kb_id` (the server
  persists it, so no change/send race), the `change` listener still PATCHes a
  dropdown switch made without sending, and every assistant message renders a
  muted `via KB: <name>` / `no KB` tag (`kbTag()` reads the persisted
  `kb_name`/`kb_id` on each message, so labels survive a reload).
  **Developer mode** toggle: when on, the message POST carries
  `developer:true` and each reply gets a collapsible `▸ developer trace`
  `<pre>` (escaped monospace, no MathJax/markdown) with rows for retrieval
  chunks / reasoning / messages / tool calls+results. Traces are live, never
  persisted; sessions render from the server's JSON.
  **⚙ Settings panel** (below the KB row, `#settingsBtn` toggles
  `#settingsPanel`; chat page only): a **Global** fieldset with the 7
  generation params (`temperature`/`top_k`/`top_p`/`min_p`/`repeat_penalty`/
  `max_tokens`/`context_length`) plus a **per-model override** fieldset (a
  model `<select>` filled from `GET /api/model` `available`, de-duped by path
  since quants repeat the repo id; same 7 fields; **clear override** empties
  the fields and saves — an all-empty entry is dropped server-side). Empty
  input = unset/inherit; a note reads "context_length applies on next model
  load". Save posts the full shape to `/api/settings` and toasts on success;
  on open the panel refreshes both settings and the installed-model list from
  the server.
  **Chat-first full-height layout + theming (2026-10-10):** `.wrap` is now
  `display:flex; flex-direction:column; height:100vh` (chat page only — the
  OCR page has its own `.wrap`), `#layout` is `flex:1; min-height:0`, `#wrap`
  `flex:1; min-width:0` (column), `#thread` dropped its `max-height:68vh`
  clamp for `flex:1; min-height:0` so it fills the viewport and scrolls
  internally, and `#inputRow` sits naturally pinned at the bottom (Enter
  still sends). `#sideToggle` (☰, first item in `#rowbar`) collapses `#side`
  via a `.hidden` class, persisted as `localStorage.chatSide`
  ("open"/"collapsed", default open, restored before first render).
  `#settingsBtn` + `#settingsPanel` are wrapped in a `position:relative`
  `#settingsWrap`; the panel is now an absolutely-positioned dropdown
  (`top: calc(100% + 6px); right: 0; min-width:360px; max-height:70vh;
  overflow:auto; z-index:50`, shadow) — the open/close `hidden` toggle JS is
  unchanged. All colors migrated to the shared tokens in `_header.html`;
  the `:root { --line … }` mini-block was removed.
- `validation/` — `pending/` (docs), `verified/<doc_id>/`, `rejected/<doc_id>/` (per-item JSON), `uploads/<doc_id>/` (pdf, md, page PNGs).
- `functions/beam_calc.py` — self-contained, **stdlib-only** (math; numpy/matplotlib/argparse/yaml dropped) ACI 318M-19 beam shear/flexure calcs extracted from the BeamValidation repo
  (github.com/Siboi420/BeamValidation, commit `668be3670dc8ba065f215a0ca1b59eb9e3bd8ca5`, `scripts/RCBeam_moment_capacity.py`). Public: `min_shear_reinf(b_w, f_c, f_yt)` → Av,min per metre (mm²/m, §9.6.3.3, `max(0.062·√f'c·b_w/f_yt, 0.35·b_w/f_yt)·1000`); `shear_capacity(b, d=None, f_c=None, A_v=0, s=0, f_yw=0, A_s=None, V_u=None, M_u=None, h=None, cover_cg=None)` → wrapped `compute_aci_shear` — effective depth is **d, or h with cover_cg (d = h − cover_cg), never both (loud XOR ValueError), rejected cover_cg ≥ h**, Vc rows: simplified `§22.5.5.1(a)`; detailed `(b)` only when stirrups ≥ Av,min AND A_s+V_u+M_u given, capped `§22.5.8.5.3`-adjacent `0.29·λ·√f'c·b·d`; **size-effect `(c)` when stirrups < Av,min (or absent) and A_s given: `λ_s = min(√(2/(1+d/250)), 1)` (§22.5.5.1.3), `V_c = 0.66·λ_s·λ·ρ_w^⅓·√f'c·b·d`**; Av,min comparison via `min_shear_reinf(b, f_c, f_yw)·s/1000` (reused, not duplicated); stirrups adequate ⇔ that inequality; φ_v=0.75; returns `Vc_criterion` ("row (a)"|"row (b)"|"row (c)") + `lambda_s` on top of the numeric keys; `flex_capacity(b, d=None, A_s=None, f_c=None, f_yl=None, h=None, cover_cg=None)` → wrapped `compute_aci_flexure` (stress block §22.2.2.1, β₁ §22.2.2.4.3, φ Table 21.2.2), same d/h XOR path. Constants EPSILON_CU=0.003, Es=2e5, λ=1.0.
  `design_beam(V_u, M_u, max_b, max_h, cover=40, f_yt=420, f_y=420, f_c_list=None, rate_conc=None, rate_steel=1.2, preset=None)` → **cheapest-feasible beam design** via a deterministic full-grid search (the LLM makes ONE call, never iterates): b in 250→max_b step 50 × h in 350→max_h step 50 × f'c in [20,25,30,35,40] × longitudinal D16/D19/D22/D25 × 1–6 bars × stirrups D10/D12/D13 (2 legs). d = h − cover − φ_long/2. Shear = simplified Vc row (a) 0.17·λ·√f'c·b·d (design intent — the row (b)/(c) variants in `shear_capacity` evaluate existing sections); V_s,req = V_u/0.75 − V_c; ≤0 → min stirrups at s_max, else s = A_v·f_yt·d/V_s,req; feasibility: φV_n ≥ V_u (auto by construction), V_s ≤ 0.66·√f'c·b·d (§22.5.1.2), 100 ≤ s ≤ s_max (§9.7.6.2.2: min(d/2,600) when V_s ≤ 0.33·√f'c·b·d else min(d/4,300)), A_v ≥ Av,min·s/1000 (folded in as the s ≤ A_v·1000/Av,min cap). Flexure = `flex_capacity` gate (φM_n ≥ M_u) plus A_s ≥ As,min §9.6.1.2 (checked here, not in `flex_capacity`). Objective = min total cost per metre = concrete (`rate_conc[f'c]·b·h/1e6`) + longitudinal steel (`rate_steel·ρ·A_s/1e6`) + stirrup steel (`rate_steel·ρ·A_v·perim/(s·1e6)`, perimeter ≈ 2(b−2c)+2(h−2c), ρ=7850 kg/m³). Returns `{"feasible", "reason", "optimum", "ranked" (top-5 by cost)}`; infeasible → `feasible:false` + reason, never raises. Unit rates: **defaults to USD placeholders** (`DEFAULT_RATE_CONC` per grade, `DEFAULT_RATE_STEEL`), not real prices; `preset="idr"` substitutes the **Indonesian-market table** (`PRICE_PRESETS["idr"]`: Rp/m³ by grade, Rp/kg BJTS 420B rebar) — the returned `cost` is in the preset's unit (Rp for idr, $ for usd); explicit `rate_conc`/`rate_steel` always beat a preset; `f_c_list`/`rate_conc` merge over defaults. `ponytail:` bar-fit/placement geometry in width b NOT checked; `ponytail:` the §9.6.3.1 no-stirrups exemption (V_u ≤ 0.5·φ·V_c) is folded into min stirrups — conservative, add if cost fidelity ever matters; `ponytail:` M_u required (shear-only sizing out of scope, pass 0).
- `functions/wrapper.py` — schema-driven dispatcher (`call_tool(name, **kwargs)` → `{value, unit, basis}`; registry maps the 4 tool names; loads the matching `schemas/<name>.json` resolved via `__file__`; validates required fields, unknown keys, numeric type/finiteness, exclusiveMinimum/minimum bounds — plus light structural checks for `array`/`object`/`string`-enum params (non-empty-list-of-finite-numbers `f_c_list`, finite-number-dict `rate_conc`, `preset` ∈ {usd, idr}); raises `ValueError` with a clear message). Schema read/parse errors (missing file, bad JSON) are wrapped as `ValueError`.
- `functions/test_shear_tools.py` — plain asserts + PASS/FAIL (no framework), 28 checks over all four tools + wrapper shape/unit/basis + d/h resolution + validation error paths (missing/negative/non-numeric/unknown-key/unknown-tool) + the design_beam suite (see Tests section); exits non-zero on failure. Loads sibling modules via `importlib` so it runs from any cwd.
- `schemas/min_shear_reinf.json`, `schemas/shear_capacity.json`, `schemas/flex_capacity.json`, `schemas/design_beam.json` — OpenAI function-calling shape (`name`/`description`/`parameters` with `type`/`properties`/`required`/`additionalProperties:false`) plus an `output` block carrying `unit` + `basis` (returned by wrapper). `d` is **optional** on the two capacity schemas (default null); `h` + `cover_cg` are optional fields resolving to d; capacity `description`s carry the few-shot line ("a 200x300 beam → pass b=200 and (d, or h=300 with cover_cg)"), `min_shear_reinf`'s a b_w-only variant and its `description` advertises the "minimum Ast" / "minimum shear reinforcement" / "minimum stirrup area" aliases (Av,min) so the model routes those phrasings to it; `shear_capacity` `basis` names row (c) + §22.5.5.1.3 λ_s. `design_beam.json` is the sizing tool: required `V_u`/`M_u`/`max_b`/`max_h`, optional `cover`/`f_yt`/`f_y`/`f_c_list`/`rate_conc`/`rate_steel`/`preset` (enum usd|idr), description advertises "given Vu and Mu, size the beam within max_b × max_h" routing (use instead of shear/flex_capacity when the question asks to DESIGN/SIZE a beam).

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
  pages now supported incrementally via `/ocr/<doc_id>` + a new `ocr_pages` range;
  `doc["ocr_pages"]` itself is left untouched — coverage still derives from items).
  Invalid values return 400.
- OCR jobs report live `done`/`total` page counts, surfaced in the auto-OCR bar,
  the upload status line, and the review-page "OCR'd X/Y pages" line.
- `testOCR` doc: page 1 cost table (HTML→markdown) + page 3 pipe table both recognized.
  **Open question:** user reports page 1 may contain a 2nd table visually that GLM-OCR
  doesn't extract; investigation was in progress (GLM-OCR directed at page 1 returns only
  the cost table). Not resolved.
- Jobs dict is in-memory (restart loses jobs). `UNSLOTH_API_KEY` is required.
- **Beam tools rows (a)/(b)/(c) (implemented 2026-09-02):** trigger semantics — `shear_capacity` reads stirrup adequacy as `A_v ≥ min_shear_reinf(b, f_c, f_yw)·s/1000`; adequate → rows (a)/(b) with `lambda_s = 1.0`; inadequate/absent + `A_s` → **row (c)** size-effect (`λ_s = min(√(2/(1+d/250)), 1)` — note d in mm, caps at 1.0 for d ≤ 250, so d=240 gives λ_s=1.0 not 0.9897); no stirrups + no `A_s` → row (a) fallback. `Vc_criterion`/`lambda_s` are additive return keys. Both capacity tools take d XOR (h+cover_cg) with loud errors for both/neither/cover≥h.
- **Eq-key/section context (resolved):** ACI pages where GLM-OCR bolds
  provision markers (`**22.5.1.2**`) previously stamped `section=None` on every
  item of that page (e.g. the whole of page 404), so equation chunks lacked a
  section anchor and models fell back to recall. Fixed at **parse** (itemizer
  regexes tolerate `**` bold prefixes — future docs) and at **render**
  (rag_uploader backfills the section from the eq key and folds the provision
  statement into equation chunks — applies to the existing verified set, no
  re-OCR: current ACI export backfills 11 eq sections, folds 3 statements).
- **KB table chunks → clean Unicode, deduped, self-contained (resolved + re-tested):**
  `_math_to_text` no longer strips `_` and emits `λ_s`/`ρ_w`/`√(f_c′)`
  (both `\lambda_s` and `\lambda_{s}` keep their `_`), fixing the qwen λ-drop
  in Table 22.5.5.1 (c) and Table 22.6.5.2. Table chunks now emit ONE canonical
  representation (caption + normalized Unicode table; raw LaTeX mirror
  dropped) plus inline `Symbols:` definitions for `A_g`/`b_w`/`b_o`/`N_u`/`β`/`α_s`.
  **Re-test 2026-09-01 (Qwen3.8-27B + tool + `rag_scope` hybrid/autoinject,
  `max_tokens: 32000`): PASS.** Table 22.6.5.2 least-of-(a)(b)(c) answer carried
  BOTH λ factors; Table 22.5.5.1 row (c) `0.66λ_sλ(ρ_w)^⅓√(f_c′)b_w·d` carried
  `λ_s`+`λ`+`ρ_w`, while row (b) correctly had NO `λ_s` (λ_s is row-(c)-only) —
  the old duplicate-content / mangled-math / dropped-λ/merged-"lambdas" class is
  gone. Caveat first seen in this re-test: bare `rag_scope` injects nothing; the
  tool must be declared (see "Querying the KB" bullet + docs).
- Equation keys are edited via dedicated eq-number/eq-letters inputs in the
  Edit + Accept editor and are never auto-re-derived on accept (None = untouched,
  "" = cleared). The draw-box has a type selector (`bboxType`, auto default;
  forced kinds append a single item via `append_bbox_item`; equation draws use
  a dedicated LaTeX-only prompt plus a defensive HTML-table-tag strip). Items can be
  deleted (item + verified/rejected copies removed).
- **Per-doc verified/rejected folders (migrated 2026-09-02):** item JSONs now
  nest one folder per `doc_id` — `verified/<doc_id>/<item_id>.json`,
  `rejected/<doc_id>/<item_id>.json` — mirroring `uploads/<doc_id>/`; the flat pile
  of all-docs files is gone (move-only migration, counts verified identical:
  705 ACI-318M-19-Metric items). File layout only: item ids, JSON shape, routes,
  and KB exports are unchanged; filenames still carry the `doc_id` prefix.
- **One server + model swap + KB/session routes (implemented 2026-09-03):** the
  Flask app on `:5000` serves OCR + chat (sessions + dev trace) + KB management +
  the model bar; `:5001` (orchestrator server) is gone. Chat model constant is
  `ibm-granite/granite-4.2-8b-GGUF` (via `config.CHAT_MODEL`); the Qwen constant and
  the hardcoded `KB_ID` are gone (name-based resolution). Unsloth verbs verified
  against the live openapi: KB rename = **`PATCH`** `/api/rag/knowledge-bases/
  {kb_id}`, delete = DELETE, unload body **requires `model_path`** (empty/unknown
  path = harmless no-op `{"status":"unloaded"}`), status = `GET
  /api/inference/status`, `load` may return before ready (models.py polls ~2s up
  to 120s, matching by GGUF filename suffix). Model swap verified live:
  `POST /api/model/load` with nothing loaded → `unloading resident` (no-op) →
  `loading GLM-OCR` → `done/loaded GLM-OCR`; second load short-circuits `already
  loaded`. Smoke test grew 132 → **212 backend-testable checks** (sessions CRUD,
  chat message echo/persist/trace-kinds, KB routes + upload incl. `__all__` via a
  stubbed `rag_uploader._api` and a temp `VERIFIED_DIR`, model swap states with
  `models.*` stubbed, header/tab render on both pages).
- **Chat markdown+LaTeX + dynamic model picker (implemented 2026-09-03):**
  chat messages render markdown (tables/bold/headers/lists via marked@15 CDN)
  and LaTeX (`\(…\)`/`\[…\]`/`$$…$$`/`$…$` via MathJax) through the math-span
  placeholder pipeline in `renderMarkdown()` (escape → protect math → marked →
  restore → typeset; DOM via `createContextualFragment`/`replaceChildren`, no
  `innerHTML`; link schemes guarded). The two hardcoded Load buttons are one
  dropdown: `GET /api/model` returns `{loaded, available, job}` where
  `available` is one entry PER INSTALLED QUANT of each cached GGUF model
  (`GET /api/models/cached-gguf` — real disk cache; the `/api/models/local`
  registry lists empty/half-installed repos with no weights and is NOT
  consulted; `mmproj-*` skipped; verified live 2026-09-03 — sam3/gemma/qwen
  stubs deleted, remaining: granite-4.2-8b (Q6_K), GLM-OCR (Q8_0),
  takes a literal model path (+ optional `variant` from the dropdown) with
  variant-aware "already loaded" matching.
  Smoke test now 238 checks.
- **Generation profiles (implemented 2026-09-15):** a global default profile
  + per-model overrides in `BASE/settings.json` (gitignored), applied to
  every chat session automatically via `profiles.resolve` — built-ins
  (`temperature 0.2`, `repeat_penalty 1.1`, `max_tokens 12000`) <- global <-
  per-model. Sampling params resolving to None are omitted from the
  `/v1/chat/completions` payload; `context_length` is load-time only
  (threaded into `models.load(max_seq_length=…)` by the model worker, takes
  effect on the next load). `/api/settings` GET/POST persists the file
  (NaN/Inf/garbage -> 400); the chat page's ⚙ Settings panel edits both
  sections. `TEMPERATURE`/`MAX_CHAT_TOKENS` are gone (moved into
  `profiles.DEFAULTS`); `ocr_engine` sampling and RAG retrieval `top_k` are
  untouched.
- **Per-query thinking toggle (implemented 2026-09-15):** `chat()` sends
  `enable_thinking` EXPLICITLY on every request (the GGUF backend loads with
  thinking on via Studio's `chat_template_kwargs`; an explicit False is the
  only per-query way to run the fast path). `answer_turn` routes: ambiguous/
  judgment phrasing (`_AMBIGUOUS_RE` keywords `should`/`recommend`/
  `dimension`/`assume`/`if`/`or`) → `thinking=True` on first pass;
  parameter-extraction questions (default) → `thinking=False`; a failed fast
  pass escalates once to `thinking=True` (empty answer, tool-arg error
  surfaced to the model, or a calc-style question — `capacity`/`shear`/
  `moment`/`flex`/`Ast`/`Av`/`kN`… — answered with no tool call). Keyword
  heuristic marked `ponytail:` (upgrade: real classifier or retry-only
  policy). Live check (2026-09-15): `enable_thinking: false` accepted by
  the :8888 backend (HTTP 200, no rejection); re-verified live on
  granite-4.2 (2026-09-26): fast path → no `reasoning_content`, thinking
  path → `reasoning` trace step + correct tool answer.
- **Manual item ordering (implemented 2026-09-03):** every item carries
  `order` — document position by default, editable per page via `POST
  /item/<doc_id>/<item_id>/order` `{order: <finite number>}` (rounded to
  nearest int, clamped to 1..n, the moved item goes to that position and the
  page renumbers 1..n; non-finite/missing -> 400). The review page and
  KB exports (`rag_uploader._item_order` prefers `order`) sort by it; the
  type-priority sort survives only as id assignment, so ids/merge-restore
  are unchanged. Drawn-box items get page-tail orders. `doc_view` runs
  `_ensure_order` once per legacy doc (`_order_stamped` marker) so existing
  docs get document order without losing review state.
- **Chat repetition loop + "minimum Ast" routing (fixed 2026-09-03):** the
  8B-model degenerate repeated-line loop (session c1f3db7e07a0) is addressed at
  the sampling level (`repeat_penalty: 1.1` on the chat payload, verified
  accepted live) plus prompt routing (re-run the tool on "break down" asks;
  "Ast" → `min_shear_reinf`). Live-verified: "what is the minimum Ast for
  b_w=350, f_c=28, f_yt=420?" calls `min_shear_reinf` with those exact args and
  answers 291.7 mm²/m. `_truncate_repetition` remains as the safety net. No new
  tool (the `min_flex_steel` idea was dropped — YAGNI; `min_shear_reinf` covers
  it); tool count was 3 until the design_beam tool below.
- **`design_beam` optimizer tool (implemented 2026-10-08, live-verified):** ONE tool that sizes a beam — given factored `V_u` (kN), `M_u` (kN·m) and section bounds `max_b`/`max_h` (mm), a deterministic full-grid search inside `beam_calc.design_beam` returns the cheapest feasible `{b, h, d, f'c, long_bar, stirrup_bar, s}` + top-5 ranked + reason. Tool count 3 → **4**. Offline checks in `test_shear_tools.py` (19 → **28 checks**): pure-shear `V_u=50` → `250×350×20, D19×1` @ ~21.8 $/m (strictly cheaper than the previously-verified b=300,d=500,f'c=30 class); `M_u=200` binds flexure (`250×350` rejected for every f'c; optimum `250×550×20, D16×6`); d-formula, Vs/spacing limits, bound iteration, infeasible-with-reason, cost-ranked determinism, wrapper array/object validation. **Two bugs found + fixed during the first live runs (2026-10-08):** (1) `rate_conc` grade keys arrive as JSON strings (`"20": 100`) and were silently **ignored** (`rates[f_c]` int lookup missed) — the custom concrete cost fell back to the default rate and the answer's cost came out wrong ($742.89 vs the true $736.59 at the same rates); now `int(k)`-normalized with a loud ValueError for non-numeric keys, and the test asserts the custom rate actually lands in `cost_concrete`; (2) `_pick_stirrups` **rejected** any combo whose exact demand spacing exceeded `s_max` — but small positive `V_s,req` is exactly where minimum stirrups at `s_max` already suffice (s_demand > s_max ⇔ Vs@max ≥ V_s,req) — so the search dropped feasible, cheaper designs and returned a sub-optimal optimum (e.g. it claimed the default-rates optimum was 300×550/D16×5 @ $40.70 when the true optimum after the fix is `250×600×20, D25×2` @ **$34.71**; b=250×600 was wrongly infeasible before). **Live re-verification (gemma-4-12B, 2026-10-08):** the custom-cost question (Vu=120, Mu=180, max 350×600, "concrete 100/m³, steel 50/kg") → one `design_beam` call with `rate_conc:{20:100,…,40:100}`, `rate_steel:50` (gemma also chose `f_y=400`, an accepted optional), tool optimum `250×600×25, D25×2, D10@274` @ **$711.08** (`cost_concrete=15.00` — the custom rate provably applied), answer reports it verbatim (~1 min wall, well under the granite loop times). **Live (granite-4.2, earlier same day):** the default-rates question converged in the ideal 2-generation shape (`message → tool_call → tool_result → message → answer`) in ~2 min wall (118.6s at ~4.5 tok/s on the 2080 Ti) after the system-prompt stop-condition; the ~16-min/8-pass 502 loop hasn't recurred, but it was stochastic model looping, so one sample proves convergence, not prevention; the structural bounds remain the 8-iteration cap → 502 + repeat_penalty + unload-with-force_cancel_active reset. `orchestrator._api` still has no HTTP timeout — a wedged backend hangs a /messages POST indefinitely (bit us twice 2026-10-08; fixed by the reset, not yet by code).
- **Voice / AIRI roadmap (later):** docs-only this round (`docs/voice-roadmap.md`);
  no voice code or new deps. Target: browser mic → STT → chat session store →
  `orchestrator.answer_turn` → TTS → playback, with planned `POST /api/voice/stt`
  and `POST /api/voice/tts` routes; AIRI (moeru-ai) is the multimodal-
  orchestration reference, adjusted to a keyboard-first assistant.

## Known gotchas / foot-guns

- **Model unload state**: if OCR suddenly errors "No model loaded", reload via
  the model bar (Load GLM-OCR) or `POST /api/inference/load
  {"model_path":"ggml-org/GLM-OCR-GGUF"}`. The unload body requires
  `model_path` (`models.unload` handles None as a no-op).
- **Model-job steps race the poller**: `/api/model/load` returns after the job
  dict is seeded with `unloading <name>`, but the worker may already be past it
  by the time you poll — assert on the final state + captured steps, never on a
  mid-flight poll. Tests capture the step INSIDE the stubbed unload/load.
- **`settings.json` is user data**: the smoke test and selftests redirect
  `profiles.SETTINGS_PATH` to a temp file — never let a route/selftest write
  the machine's real settings. Editing `context_length` changes only the NEXT
  model load (the backend is single-model; a resident model keeps its loaded
  context), and an editing-only change to sampling params applies immediately
  to subsequent chat turns.
- **Granite GGUF download is the user's step**: `models.load("chat")` posts to
  the backend and polls up to 120s; if granite isn't downloaded yet the load
  stalls/errors on the hub side — that's outside our code.
- **Chat turns are non-streaming and synchronous** in the request thread: with a
  real model a `/messages` POST can take tens of seconds; the UI disables Send
  meanwhile. No SSE this round.
- **`__all__` KB upload reads the machine's real `validation/verified/`**: the
  smoke test points `rag.VERIFIED_DIR` at a temp dir; the route itself is
  intentionally broad (all verified docs).
- **Active generations**: non-streaming chat calls (what ocr_engine.py sends) are NOT
  cancellable via `POST /api/inference/cancel` (returns `cancelled: 0`, they're not
  registered). The working kill is unload with `force_cancel_active: true`. If you need
  cancelable OCR, add `"stream": true` to the payload.
- **Chat math spans must stay whole for marked**: the chat markdown pipeline
  swaps `\(…\)`/`\[…\]`/`$$…$$`/`$…$` out for NUL placeholders BEFORE
  `marked.parse` — marked's inline escape rule otherwise strips the `\(`/`\[`
  backslashes and mangles `_`/`^` inside math. Restoring placeholders after
  parsing (before DOM insertion) is what keeps MathJax math intact; NUL can't
  occur in real content. The trace `<pre>` and error paths bypass the pipeline
  on purpose (plain text only).
- **The backend now ENFORCES the `model` field (verified live 2026-09-03)**: with
  "Switch model by request" off, `/v1/chat/completions` rejects any model name
  other than the currently-loaded one (HTTP 404 `model_not_found`) — the old
  "field ignored, backend answers with the loaded model" assumption is dead.
  `orchestrator.chat()` and `ocr_engine` both send the loaded model via a local
  `_loaded_model()` helper (`models.current_model()`, falling back to the
  config default when the status query fails or nothing is loaded), so chat/OCR
  follow whatever the model bar loaded — granite, LFM2.5, or GLM-OCR all work
  without the 404 mismatch.*
- **App respawns**: don't assume a `kill`/`pkill` sticks — verify with `curl`.
- **WSL GPU**: `/dev/dxg` is the paravirtualized path; `nvidia-smi` works via
  `/usr/lib/wsl/lib/nvidia-smi`. Unsloth's `POST /api/llama/backend` is the supported
  way to change llama.cpp build (cpu/cuda/rocm/vulkan).
- Do not paste the API key into code; it lives in `.env.local` (gitignored) and env only.
- Do not fabricate results; verify against actual tool output (tests, curl, logs).

## Tests / verification

- `python3 test_itemizer.py` — 73 itemizer assertions.
- `python3 functions/test_shear_tools.py` — 28 hand-calc checks for the `functions/` shear/flexure tools (Av,min mm²/m, simplified Vc, row (c) size-effect Vc incl. λ_s + ρ_w, adequate-stirrup rows (a)/(b), partial stirrups → row (c) + V_s, d↔h/cover_cg equivalence for shear and flex, d-resolution errors (XOR/neither/cover≥h), row (a) fallback, wrapper shape + unit/basis incl. h-path, validation error paths; plus 9 `design_beam` checks: pure-shear optimum 250×350×20 D19×1 (with the stirrup-clamp regression guard: s == s_max == d/2 and Vs > 0) cheaper-or-equal to the b=300,d=500 class, min-stirrup path vs Av,min, flexure gate binds (φM_n ≥ M_u, A_s ≥ As,min, no 250×350 row at M_u=200, optimum 250×550×20 D16×6), d = h − cover − φ_long/2 (incl. custom cover), Vs ≤ 0.66·√f'c·b·d + s ∈ [100, s_max] + s_max switch, bound/step iteration (max_b=385 → b ≤ 385), infeasible → `[]` + reason (V_u=300 and M_u=900 cases), cost-ranked top-5 determinism, wrapper registration + array/object validation errors + string-key `rate_conc` applied (cost_concrete equals the custom rate·b·h/1e6) + non-numeric grade key → ValueError; plain asserts + PASS/FAIL, exit non-zero on failure.
- `python3 smoke_test.py` — 238 end-to-end checks via Flask test client (no live OCR,
  no backend; `rag_uploader._api`, `models.current_model/unload/load/list_models`,
  and `orchestrator.answer_turn` stubbed where they'd hit :8888):
  the original 132 assert `?ocr=1` redirect, no-key OCR error,
  `parse_page_range`/`parse_page_ranges`
  valid+invalid forms, `ocr_pages` storage incl. multiple ranges, invalid -> 400,
  md-wins-over-range, NaN bbox coords rejected, equation key accept/preserve/clear,
  `append_bbox_item` kinds (incl. HTML-table-wrapper strip for equation/text,
  raw-keep for table), bbox `type` validation, item delete, incremental `/ocr`
  routes (no-range/invalid/all-covered -> 400, uncovered/mixed -> 200 + job_id +
  skipped count, form-field fallback, clamp beyond PDF, fully-beyond -> 400),
  `merge_ocr_markdown` unit checks (append order, replace-in-range, page 1 and
  `# OCR:` header preserved), and `merge_pages_into_doc` review-state
  preservation (verified status + edited content survive a merge, new pages land
  pending, drawn-box items re-attached);
  the new 54 add session CRUD (create → list → rename → kb_id set/clear →
  delete, 400 on blank/no name, 404 on missing/malformed sid), chat message
  route (echo answer, no `trace` key without developer, history persisted with
  contents, `developer:true` returns the expected trace kinds, session `kb_id`
  threaded through `answer_turn`, message-body `kb_id` override + persist,
  assistant messages tagged `kb_id`/`kb_name` — raw-id fallback, display-name
  resolution, null-KB — 400 empty content, 404 missing session),
  KB routes (list/create/rename/delete issue the right Unsloth calls + shapes,
  blank names -> 400, bad kb id -> 404, single-doc + `__all__` upload with
  expected `uploaded` filenames via a temp `VERIFIED_DIR`), model API
  (`/api/model` reflects stubbed status + the `available` list (no `key`
  field) + the in-flight `job` field (running synthetic job → exposed; cleared
  on done/idle), already-loaded short-circuits by path suffix with no calls,
  missing/empty model path -> 400, cold swap `unload` before `load` with
  unloading/loading steps captured in-stub and literal config paths passed
  through, warm swap unloads the GLM path, per-quant `variant`
  threaded through `models.load` (explicit variant beats the chat config
  pin), already-loaded matches the loaded variant (same-quant no-op,
  different-quant proceeds), load error -> `error` propagated),
  and shared-header renders on both pages (active tab + model `#modelSel`
  dropdown + `#loadBtn`; `/chat` also carries the marked CDN script).
  the new 22 add manual ordering: `POST /item/.../order` moves an item to a
  1-based position and renumbers its page to contiguous 1..n (moved item
  holds the target, the old position-1 holder shifts to 2; 99 clamps to the
  tail, 0 -> 1, 2.7 rounds to 3; missing/non-numeric/NaN/inf -> 400, unknown
  item -> 404),
  `order` carried into accepted+rejected JSON payloads, a fresh `parse_document`
  re-stamps document order, `_ensure_order` on a legacy doc (stamp marker,
  every item stamped, no resurrection of a deleted item, bbox item tails the
  page, review status/edited content preserved), and `merge_pages_into_doc`
  preserving a manually-edited `order`; bbox helpers assign order.
  the new 10 (223 -> 233) add generation profiles: `GET /api/settings` empty
  when no file, `POST` sanitize + unknown-key drop + persist round-trip,
  NaN -> 400, non-object global -> 400, the model worker threading a profile
  `context_length` into `models.load(..., max_seq_length=4096)`, empty
  per-model override clearing the entry, the chat route calling
  `answer_turn` with no positional max-tokens, and `/chat` rendering the
  settings panel (global + per-model fieldsets).
  the new 5 (233 -> 238) add dark theme + chat layout: `/` and `/chat` both
  carry `<html lang="en" data-theme="dark">` + the anti-FOUC head
  script (`localStorage.getItem('theme') || 'dark'`) + the shared
  `#themeBtn`, `/` carries the dark token palette (`--bg:#0f172a`,
  `--surface:#1e293b`, `--line:#334155`), `/chat` carries `#sideToggle` +
  `#thread` + `#inputRow` + `#settingsWrap` (+ `#settingsPanel`/
  `#settingsBtn` nested) and preserves `modelSel`/`loadBtn`/`settingsSave`/
  `moSel`/`g-context_length`/`kbSel` + tab links + the marked CDN.
- `python3 profiles.py --selftest` — offline: defaults/global/per-model
  resolution (per-model beats global beats built-ins), resolved-snapshot-path
  matching against a repo-id key (base name, `-GGUF` stripped), sanitize
  (numeric-string coercion, unknown-key drop, empty/null -> dropped,
  NaN/Inf/garbage/fractional-int -> ValueError, empty per-model entry dropped,
  non-object sections rejected), and corrupt-file -> defaults never raise.
- `python3 orchestrator.py --selftest` — offline PASS: schema glob yields exactly
  the 4 tool names in the right shape (design_beam, flex_capacity, min_shear_reinf,
  shear_capacity), tool-call extraction parses both synthetic
  native `tool_calls` and `<tool_call>`-marker payloads, round-trip/`_norm_args`
  shapes, and a `run_loop` trace-shape assert with `chat()` stubbed (expected
  step kinds/order `reasoning, message, tool_call, tool_result, reasoning,
  message, answer`, real wrapper call value 291.67) plus `_truncate_repetition`
  checks (8× repeated 60-char line truncated to prefix + note; clean text,
  short lines, and 3× repeats pass through untouched) and chat-payload
  asserts with `_api` + `profiles.load_settings` stubbed (defaults:
  `temperature 0.2`, `repeat_penalty 1.1`, `max_tokens 12000`, no
  top_k/top_p/min_p keys when unset; override: global `temperature 0.7` +
  `top_k 33` and per-model `min_p 0.05` flow into the payload, unset
  `repeat_penalty` keeps the default, `None` max_tokens -> profile default;
  `enable_thinking` EXPLICIT per request — default chat() sends false,
  explicit `thinking=True` sends true) and `answer_turn` routing/retry
  decisions with `run_loop` + `retrieve` stubbed (parameter-extraction
  question → fast first pass then one escalation on an empty answer;
  ambiguous keyword question → thinking on the FIRST pass, no retry;
  calc-style no-keyword question answered without a tool call → fast then
  retry; **no-KB (kb_id=None) sessions use `SYSTEM_PROMPT_BARE` and the
  "no knowledge base attached" context on every pass, KB sessions use the
  RAG `SYSTEM_PROMPT` + the retrieved chunk text**).
  No HTTP-handler block — the server is gone.
- `python3 rag_uploader.py --selftest` — offline grouping/ordering/Unicode/empty-skip
  checks (unchanged but now with `_api` raising RuntimeError).
- `python3 models.py --selftest` — offline: `MODELS` wiring + `load("chat")`
  payload shape (`model_path`/`force_reload`/`max_seq_length=32768`,
  `gguf_variant` present for the chat default; explicit `variant` on a
  literal path wins over the config pin; explicit `max_seq_length=4096` on
  a chat load wins over the 32768 config default while the variant pin
  still applies), literal
  path `load("some/local/model-GGUF")` posts that path with no
  `max_seq_length`/`gguf_variant`, and `list_models()` hits `GET
  /api/models/cached-gguf` and emits one entry per installed quant parsed
  from the snapshot filenames (`mmproj-*` vision projectors skipped).
- A quick GPU/alive check: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/`
  (app) and `:8888` (unsloth); `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader`
  should show >0% during OCR.
