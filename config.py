"""Shared configuration for the OCR validation app."""
import os

API_BASE = os.environ.get("UNSLOTH_API_BASE", "http://localhost:8888")
API_KEY = os.environ.get("UNSLOTH_API_KEY")  # no hardcoded fallback
MODEL = "ggml-org/GLM-OCR-GGUF"  # OCR model (served by Unsloth Studio)
CHAT_MODEL = "ibm-granite/granite-4.2-8b-GGUF"  # chat model (download handled by the user)
# GGUF quant for CHAT_MODEL. Pins the single cached quant for the repo
# (granite-4.2-8b-Q6_K.gguf) via the load endpoint's `gguf_variant` field —
# the backend REJECTS a ":Q6_K" suffix inside model_path. (The old
# granite-4.1 repo needed an explicit pin for the same reason: its backend
# default UD-Q4_K_XL was not cached while UD-Q6_K_XL was.)
CHAT_GGUF_VARIANT = "Q6_K"
# context_length override for the chat model: Qwen/granite-family models need
# an explicit max_seq_length on load (backend default without it was 17408 for
# Qwen 3.8). None -> omit the field (backend default).
CHAT_MAX_SEQ_LENGTH = 32768
OCR_MAX_SEQ_LENGTH = None
UPLOAD_DIR = os.environ.get(
    "VALIDATION_UPLOAD_DIR",
    os.path.join(os.path.dirname(__file__), "validation", "uploads"),
)