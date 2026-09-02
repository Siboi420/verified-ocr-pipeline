# Verified OCR pipeline

Local OCR-validation + chat toolkit for structural/seismic engineering
research (Kresna's MEXT / SSI-lateral research).

**Source PDF → GLM-OCR → human review → verified data → RAG knowledge base →
ask questions.**

A single Flask server on `:5000` serves everything — two browser-style tabs:

- **OCR Validation** — upload/load a PDF, OCR it with GLM-OCR, review
  item-by-item (tables, equations, text), accept / edit / reject / skip, and
  export verified JSON. Draw a box on the page to OCR just that region.
- **Chat** — multi-turn sessions against the "Verified OCR" KB (hybrid RAG,
  top_k=3) + 3 ACI beam calculation tools (shear/flexure). Developer mode
  shows the retrieval / reasoning / tool-call trace.

Both pages share a top bar with the **model control**: which model is loaded
(GLM-OCR for OCR, granite for chat; only one resident at a time) and buttons
to load/unload — a swap is always unload-before-load with a progress toast.

Knowledge bases (Unsloth RAG) can be listed / created / renamed / deleted and
fed verified items from the OCR tab (one doc or all).

## Requirements

- WSL2 / Linux, Python 3, a GPU (RTX 2080 Ti 11GB works)
- [Unsloth Studio](https://github.com/unslothai/unsloth) running on `:8888`
  (serves the GGUF models + RAG)
- `UNSLOTH_API_KEY` in `.env.local` (see below)
- The two GGUF models available to Unsloth:
  - `ggml-org/GLM-OCR-GGUF` (OCR)
  - `unsloth/granite-4.1-8b-GGUF` (chat — the GGUF download is a one-time
    step, triggered from the Unsloth UI's model-load; the app only loads
    what's cached)

```bash
pip install -r requirements-ocr.txt
cp .env.local.example .env.local   # set UNSLOTH_API_KEY
```

## Run

Two services, two separate commands each (kill + start in one line kills the
shell — `pkill -f` matches its own command line):

```bash
# 1) Unsloth Studio (:8888)
pkill -f "unsloth[_]studio" || true                      # stop
nohup unsloth studio > /tmp/unsloth-studio.log 2>&1 &    # start (wait ~10s)

# 2) The app (:5000)
pkill -f "python3 app\.py" || true                       # stop
cd ~/Projects/seismic-ai-tools && set -a; . ./.env.local; set +a; \
  nohup python3 app.py > /tmp/seismic-app.log 2>&1 &     # start

# verify
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8888/   # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/   # 200
```

Open <http://127.0.0.1:5000>.

## Tests

```bash
python3 test_itemizer.py                 # 68 itemizer assertions
python3 functions/test_shear_tools.py    # 19 beam tool checks
python3 orchestrator.py --selftest       # chat loop trace checks (offline)
python3 rag_uploader.py --selftest       # KB render checks (offline)
python3 models.py --selftest             # model mgmt wiring (offline)
python3 smoke_test.py                    # 186 end-to-end route checks (no
                                              # live OCR)
```

## Layout

- `app.py` — the Flask server (OCR review + chat sessions + KB routes + model bar)
- `ocr_engine.py` / `itemizer.py` — GLM-OCR client, markdown → review items
- `rag_uploader.py` — verified JSON → Unsloth RAG KB (render + upload)
- `orchestrator.py` — RAG + tool-calling chat engine (CLI + importable by app)
- `models.py` — Unsloth model load/unload/status
- `functions/` — ACI 318M-19 beam shear/flexure tools (`beam_calc.py`,
  schema-driven `wrapper.py`)
- `schemas/` — OpenAI function-calling tool schemas
- `templates/` — `_header.html` (tabs + model bar), `index.html` (OCR),
  `chat.html` (chat + dev trace)
- `docs/` — infrastructure, KB query guidance, corrections, voice roadmap
- `validation/` — pending docs, verified/rejected item JSON (gitignored)
- `sessions/` — chat sessions (gitignored)

## More

- `AGENTS.md` — detailed, always-synced technical reference for AI assistants
  (and anyone digging into the internals)
- `docs/rag-query-guidance.md` — how to query the KB for table/formula questions
- `docs/voice-roadmap.md` — planned voice / AIRI integration (docs only, not
  implemented)
