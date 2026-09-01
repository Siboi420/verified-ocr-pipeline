# RAG query guidance — "Verified OCR" KB (tables & formulas)

How to query the `Verified OCR` knowledge base (`24895fae-4771-4381-b7e8-75c4ee7b5bae`,
Unsloth Studio at `:8888`) when the question involves code tables or formulas.
Discovery + documentation only: no ingestion or renderer changes were made.
Shapes below were confirmed against the live API (2026-09-01).

## Verified API facts

`POST /api/rag/search` body (all optional except `query`):

```json
{
  "query": "…",
  "kb_id": "24895fae-4771-4381-b7e8-75c4ee7b5bae",
  "mode": "hybrid",      // default IS "hybrid" — passing it is explicit, not required
  "top_k": 10,           // int, 1–50, default 10
  "min_score": 0.0       // number, default 0.0
}
```

Response: `{"results": [{chunkId, documentId, filename, page, score, text}]}`
— the chunk text is in `text`, not `content`. `page` is `null` for these chunks
(chunks span many items/pages, see "Chunking note" below). Auth:
`Authorization: Bearer $UNSLOTH_API_KEY`. KB endpoints need no loaded model.

Chat path — retrieval is a *tool*: `rag_scope` is the hidden scope for the
`search_knowledge_base` function, so you must declare the tool and enable tool
calling or nothing is injected (verified: bare `rag_scope` left `prompt_tokens`
at 117 — no chunk text in the prompt):

```json
"tools": [{"type": "function", "function": {
  "name": "search_knowledge_base",
  "description": "Search the Verified OCR knowledge base"
}}],
"enable_tools": true,
"rag_scope": {
  "kb_id": "24895fae-4771-4381-b7e8-75c4ee7b5bae",
  "mode": "hybrid",
  "default_top_k": 10,
  "autoinject": true
}
```

All `rag_scope` fields optional; `kb_id` is snake_case (not `kbId`). Other
accepted keys: `thread_id`, `autoinject_min_score`.

## Recipes

1. **Hybrid retrieval** (default) — use `mode: "hybrid"` for anything symbol-heavy.
   BM25 matches exact tokens (`V_c`, `ρ_w`, `λ_s`, `Av`) that dense-only recall
   misses; dense covers paraphrased questions ("member without shear reinforcement").
   Verified: hybrid surfaces chunks carrying `λ_s` / `ρ_w`, and the Table 22.5.5.1
   chunk is rank 0 for `"Vc nonprestressed member without shear reinforcement"`.

2. **Chat path** — declare the `search_knowledge_base` tool + `enable_tools: true`
   + `rag_scope` `{kb_id, mode:"hybrid", default_top_k: 10, autoinject: true}`
   (see above). With retrieval on, a formula-table question pulls all rows (and
   the folded provision statement + notes) before answering — verified
   `prompt_tokens` jumps to ~4–8k with injected chunks.

3. **Branch-first prompt instruction** — for code tables, tell the model to state
   which row/branch applies before committing. Working example wording:
   > "For any code table, state which row/branch applies before committing: e.g.
   > `Av ≥ Av,min` vs `Av < Av,min` for Table 22.5.5.1, and use the least-of-(a)(b)(c)
   > rule for Table 22.6.5.2."

4. **Provenance** — chunks carry `## page N table — 22.x.x.x` and
   `## page N equation · eq(22.5.5.1.3)` headers (also `text — section` for
   provisions/commentary). Ask the model to quote them in the answer.

## Chunking note (informational, out of scope)

Server-side chunking is coarse: one chunk spans many items/pages (`page: null`,
e.g. a chunk holding pages 406–407 incl. Table 22.5.5.1 + 22.5.5.1.3). It still
retrieves correctly at `top_k: 10`, so nothing to do now. "One table = one chunk"
would need upload-side chunking control in `rag_uploader.py` (out of scope —
revisit only if tables start failing retrieval).

## Verification (2026-09-01)

Search API (read-only curls):

- `mode` omitted vs `"hybrid"` → identical rank-0 Table 22.5.5.1 chunk.
- `top_k` honored (3 requested → 3 results).
- `min_score` filters a component score, not the displayed RRF score: `0.04` (above
  every displayed score, ~0.03) still returned 10; `0.9` (above every dense score,
  ~0.77) returned 0. Leave at `0.0` unless cutting low-confidence dense candidates.

Chat-path re-test of the fixed table chunks (Qwen3.8-27B, tool + rag_scope as
above, `max_tokens: 32000` because the model burns tokens on reasoning_content):

- Bare `rag_scope` alone: `prompt_tokens: 117`, no injection, hallucinated answer
  (`v_c = 0.33√f'c · t/d` — invented `t/d`). This is how the recipe above got the
  tool declaration.
- With tool + `enable_tools: true` + `rag_scope`: `prompt_tokens: 8478`,
  **correct** answer for Table 22.6.5.2 (least of (a)(b)(c):
  `0.33λ_sλ√(f'c)`, `(0.17+0.33/β)λ_sλ√(f'c)`, `(0.17+0.083α_sd/b_o)λ_sλ√(f'c)`),
  both λ factors present, provenance cited (page 418).
- Table 22.5.5.1 with branch-first instruction: row (c) Av < Av,min
  `0.66λ_sλ(ρ_w)^⅓√(f'c)b_w·d` — **λ_s, λ and ρ_w all present**; row (b)
  Av ≥ Av,min correctly has NO λ_s (λ_s is row-(c)-only). The prior qwen
  λ-drop / merged-"lambdas" failure is gone.