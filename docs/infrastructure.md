# Seismic Engineering AI Assistant — Infrastructure Reference

## Project Location

All files live under ~/Projects/seismic-ai-tools/

## Three Runtime Layers

1. RAG Corpus
   - Unsloth Studio KB on :8888
   - Document chunks with metadata (source, section, page)
   - Vector search via POST /api/rag/search

2. Python Tools
   - ~/Projects/seismic-ai-tools/functions/
   - Deterministic, test-verified Python functions
   - Each has a JSON schema for tool calling
   - New functions can be added at any time without retraining

3. Fine-tuned Model
   - Qwen3.8-27B running on Unsloth Studio :8888
   - Handles orchestration, tool calling, citation, guardrails
   - Also serves GLM-OCR for document extraction (swap as needed)

## Validation App

Flask web UI at localhost:5000
- Side-by-side layout: PDF page rendered as PNG on left, OCR output on right
- Editable table grid and LaTeX equation field
- Actions per item: Accept, Edit + Accept, Reject, Skip
- Verified items go to ~/Projects/seismic-ai-tools/validation/verified/
- Pending items in ~/Projects/seismic-ai-tools/validation/pending/

Double-column pages (e.g. ACI CODE / COMMENTARY layouts): the app does **not**
differentiate columns. `ocr_engine.py` sends one full-page image to GLM-OCR
and gets a single linearized markdown stream; `itemizer.py` only splits on
`--- Page N ---` separators. CODE and COMMENTARY content are therefore
interleaved, with loose `CODE`/`COMMENTARY` headings kept as ordinary text
(see `test-ocr-files/testOCR7page.md`).

## Orchestrator

~/Projects/seismic-ai-tools/orchestrator.py
- Thin script (~200 lines)
- Flow: RAG query -> build prompt with tool schemas -> send to model -> execute tool call if needed -> return answer with citations
- No model retraining required for new tools or documents

## Data Flow

Source PDF -> GLM-OCR -> Validation App (human review) -> verified data

Verified data goes three directions:
- Tables and equations -> RAG corpus for retrieval
- Equations for tools -> LaTeX to Python spec -> implementation -> test -> function library
- Behavioral examples -> fine-tune dataset (Phase 5+)

## Stack

- Python 3, Flask
- Unsloth Studio API at localhost:8888/v1
- GLM-OCR (GGUF format) for document extraction
- llama.cpp backend
- Running on WSL, RTX 2080 Ti 11GB

## Directory Structure

```
~/Projects/seismic-ai-tools/
  functions/          # Python calculation tools
  schemas/            # JSON tool definitions
  validation/
    pending/          # Items awaiting review
    verified/         # Approved items
    rejected/         # Sent back for re-OCR
  docs/               # Documentation
  ocr_engine.py       # GLM-OCR batch extraction script
  orchestrator.py     # Orchestration script (to be built)
```

## Phase 1 Scope Summary

Sources: ACI 318M-19 (shear/torsion), ASCE 7-22 (base shear/drift), FEMA P-2091 (SSI), NIST GCR 11-917-15 (GM selection)

Tools to build first:
- min_shear_reinf() from ACI 318M-19 section 9.6.3.4
- min_torsion_reinf() from section 9.6.4
- elf_base_shear() from ASCE 7-22 section 12.8
- story_drift_check() from section 12.8.6

Guardrails:
- No arithmetic from memory, always call the tool
- Cite every source
- Flag uncertainty if input is ambiguous
- No engineering judgment (redirect to engineer)
- No data fabrication (say "not in my sources")