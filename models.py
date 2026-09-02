"""Model management for Unsloth Studio (:8888): which model is loaded, unload,
load. stdlib urllib only, same _api pattern as rag_uploader. __main__ has a
small offline self-check (no server needed).

The backend is single-model: only one model can be resident at a time, so a
swap must always unload-before-load. `unload(force=True)` carries
force_cancel_active:true to kill non-cancellable in-flight generations
(ocr_engine sends non-streaming calls, which are not cancellable via cancel).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402 — sys.path juggling must run first

API_BASE = config.API_BASE
MODELS = {"ocr": config.MODEL, "chat": config.CHAT_MODEL}


def _api(method, path, data=None):
    req = urllib.request.Request(
        API_BASE + path, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {config.API_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
        raise RuntimeError(f"api {method} {path} failed: {e}") from e


def current_model():
    """Path of the currently loaded model (or None). Non-fatal errors surface
    as RuntimeError, same as every other Unsloth call."""
    body = _api("GET", "/api/inference/status")
    active = body.get("active_model") or (body.get("loaded") or [None])[0]
    return active or None


def unload(model_path, force=True):
    """POST /api/inference/unload. model_path names the model to unload (None
    = any model); a path that isn't loaded is a harmless no-op (status
    \"unloaded\")."""
    body = {"model_path": model_path or "", "force_cancel_active": bool(force)}
    return _api("POST", "/api/inference/unload", body)


def load(key):
    """POST /api/inference/load for MODELS[key]; returns the parsed response.
    Raises RuntimeError on a missing key or an API failure."""
    if key not in MODELS:
        raise RuntimeError(f"unknown model key {key!r} (expected one of {sorted(MODELS)})")
    body = {"model_path": MODELS[key], "force_reload": True}
    if config.OCR_MAX_SEQ_LENGTH and key == "ocr":
        body["max_seq_length"] = config.OCR_MAX_SEQ_LENGTH
    if key == "chat" and config.CHAT_MAX_SEQ_LENGTH:
        body["max_seq_length"] = config.CHAT_MAX_SEQ_LENGTH
    return _api("POST", "/api/inference/load", body)


def _selftest():
    """Offline checks (no server, no key): constant wiring and the load
    payload shape."""
    assert config.CHAT_MODEL == "unsloth/granite-4.1-8b-GGUF", config.CHAT_MODEL
    assert MODELS == {"ocr": config.MODEL, "chat": config.CHAT_MODEL}, MODELS
    assert config.CHAT_MAX_SEQ_LENGTH == 32768
    assert config.OCR_MAX_SEQ_LENGTH is None

    sent = {}
    saved_api, saved_max = _api, config.CHAT_MAX_SEQ_LENGTH
    try:
        def fake(method, path, data=None):
            sent["method"], sent["path"], sent["data"] = method, path, data
            return {"ok": True}
        globals()["_api"] = fake
        load("chat")
        assert sent["method"] == "POST" and sent["path"] == "/api/inference/load"
        assert sent["data"]["model_path"] == config.CHAT_MODEL
        assert sent["data"]["force_reload"], "load must set force_reload"
        assert sent["data"]["max_seq_length"] == 32768
    finally:
        globals()["_api"] = saved_api
        config.CHAT_MAX_SEQ_LENGTH = saved_max
    print("models: selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="offline checks, no server")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return 0
    print("no CLI action (library only; use the app UI); try --selftest",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())