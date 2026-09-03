"""Shared configuration for the OCR validation app."""
import os

API_BASE = os.environ.get("UNSLOTH_API_BASE", "http://localhost:8888")
API_KEY = os.environ.get("UNSLOTH_API_KEY")  # no hardcoded fallback
MODEL = "ggml-org/GLM-OCR-GGUF"  # OCR model (served by Unsloth Studio)
CHAT_MODEL = "unsloth/granite-4.1-8b-GGUF"  # chat model (download handled by the user)
# GGUF quant for CHAT_MODEL. The backend's DEFAULT variant for this repo is
# UD-Q4_K_XL (not cached -> triggers a long HTTP re-download); the already-
# cached quant is UD-Q6_K_XL, so it must be pinned explicitly. Passed to the
# load endpoint as its `gguf_variant` field — the backend REJECTS a
# ":UD-Q6_K_XL" suffix inside model_path.
CHAT_GGUF_VARIANT = "UD-Q6_K_XL"
# context_length override for the chat model: Qwen/granite-family models need
# an explicit max_seq_length on load (backend default without it was 17408 for
# Qwen 3.8). None -> omit the field (backend default).
CHAT_MAX_SEQ_LENGTH = 32768
OCR_MAX_SEQ_LENGTH = None
UPLOAD_DIR = os.environ.get(
    "VALIDATION_UPLOAD_DIR",
    os.path.join(os.path.dirname(__file__), "validation", "uploads"),
)