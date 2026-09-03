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


def list_models():
    """[{"path", "name"}] of the models installed in Unsloth Studio.

    The backend lists them under GET /api/models/list as a `models` array of
    {id, name, ...}; we normalize defensively and append the config defaults
    (GLM-OCR is a custom install the backend list doesn't include, so the
    dropdown would otherwise lose it). Raises RuntimeError on API failure.
    """
    body = _api("GET", "/api/models/list")
    raw = body if isinstance(body, list) else \
        body.get("models") or body.get("available") or []
    out = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        path = m.get("model_path") or m.get("id") or m.get("name")
        if not path:
            continue
        name = m.get("name") or m.get("model_name") or Path(path).name
        out.append({"path": path, "name": name})
    for default in (config.MODEL, config.CHAT_MODEL):
        if default and not any(o["path"] == default for o in out):
            out.append({"path": default, "name": Path(default).name})
    return out


def unload(model_path, force=True):
    """POST /api/inference/unload. model_path names the model to unload (None
    = any model); a path that isn't loaded is a harmless no-op (status
    \"unloaded\")."""
    body = {"model_path": model_path or "", "force_cancel_active": bool(force)}
    return _api("POST", "/api/inference/unload", body)


def load(model):
    """POST /api/inference/load for a registered key or a literal path.
    MODELS keys ("ocr"/"chat") resolve to their config-default path; anything
    else is treated as the path itself. Returns the parsed response; raises
    RuntimeError on an API failure."""
    path = MODELS.get(model, model)
    body = {"model_path": path, "force_reload": True}
    if path == config.CHAT_MODEL and config.CHAT_MAX_SEQ_LENGTH:
        body["max_seq_length"] = config.CHAT_MAX_SEQ_LENGTH
    if path == config.MODEL and config.OCR_MAX_SEQ_LENGTH:
        body["max_seq_length"] = config.OCR_MAX_SEQ_LENGTH
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
        # literal path: posted verbatim, no max_seq_length (backend default)
        load("some/local/model-GGUF")
        assert sent["data"]["model_path"] == "some/local/model-GGUF"
        assert sent["data"]["force_reload"]
        assert "max_seq_length" not in sent["data"], \
            "non-default paths get no max_seq_length"
        # config-MODEL path still gets its role's length override
        if config.OCR_MAX_SEQ_LENGTH:
            load(config.MODEL)
            assert sent["data"]["max_seq_length"] == config.OCR_MAX_SEQ_LENGTH
        # list_models normalization: {models:[{id,name}]} + config defaults
        def fake_models(method, path, data=None):
            return {"models": [{"id": "unsloth/Other-GGUF", "name": "other"}],
                    "default_models": []}
        globals()["_api"] = fake_models
        got = list_models()
        ids = [o["path"] for o in got]
        assert {"path": "unsloth/Other-GGUF", "name": "other"} in got
        assert config.MODEL in ids and config.CHAT_MODEL in ids, \
            "config defaults appended when the backend list lacks them"
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