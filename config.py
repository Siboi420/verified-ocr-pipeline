"""Shared configuration for the OCR validation app."""
import os

API_BASE = os.environ.get("UNSLOTH_API_BASE", "http://localhost:8888")
API_KEY = os.environ.get("UNSLOTH_API_KEY")  # no hardcoded fallback
MODEL = "ggml-org/GLM-OCR-GGUF"
UPLOAD_DIR = os.environ.get(
    "VALIDATION_UPLOAD_DIR",
    os.path.join(os.path.dirname(__file__), "validation", "uploads"),
)