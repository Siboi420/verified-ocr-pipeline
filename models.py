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


def _cached_gguf_files(cache_path):
    """Sorted GGUF filenames cached under a cache_path (snapshots/*).
    Skips vision projectors (mmproj-*, which are not loadable models) and
    incomplete downloads. Empty on a missing/invalid path."""
    out = []
    if not cache_path:
        return out
    snap_dir = Path(cache_path) / "snapshots"
    if not snap_dir.is_dir():
        return out
    for snap in sorted(p for p in snap_dir.iterdir() if p.is_dir()):
        for f in sorted(snap.glob("*.gguf")):
            if f.name.startswith("mmproj-"):
                continue
            out.append(f.name)
    return out


def list_models():
    """[{"path", "name", "variant"?}] of the GGUF models actually cached on
    disk — one entry per installed quant, so several quants of one repo are
    separately selectable.

    The backend's model registry (/api/models/local) also lists empty or
    half-installed repos with no weights on disk, so the source of truth is
    the real disk cache (/api/models/cached-gguf). Each cached repo
    contributes one entry per quant found in its snapshot dir, named
    "<repo> (<quant>)". Raises RuntimeError on API failure.
    """
    body = _api("GET", "/api/models/cached-gguf")
    out = []
    for c in body.get("cached") or []:
        repo = c.get("repo_id")
        if not repo:
            continue
        base = Path(repo).name
        if base.endswith("-GGUF"):
            base = base[:-5]  # "granite-4.1-8b-GGUF" -> "granite-4.1-8b"
        for fn in _cached_gguf_files(c.get("cache_path")):
            quant = fn[:-5] if fn.endswith(".gguf") else fn  # strip .gguf
            if quant.startswith(base + "-"):
                quant = quant[len(base) + 1:]
            out.append({"path": repo, "name": f"{base} ({quant})",
                        "variant": quant})
    return out


def unload(model_path, force=True):
    """POST /api/inference/unload. model_path names the model to unload (None
    = any model); a path that isn't loaded is a harmless no-op (status
    \"unloaded\")."""
    body = {"model_path": model_path or "", "force_cancel_active": bool(force)}
    return _api("POST", "/api/inference/unload", body)


def load(model, variant=None, max_seq_length=None):
    """POST /api/inference/load for a registered key or a literal path.
    MODELS keys ("ocr"/"chat") resolve to their config-default path; anything
    else is treated as the path itself. `variant` pins the GGUF quant via the
    load endpoint's `gguf_variant` field (falls back to the chat config
    pin). `max_seq_length` (from the generation profile's context_length)
    wins over the per-role config default when set; None keeps today's
    behavior exactly. Returns the parsed response; raises RuntimeError on an
    API failure."""
    path = MODELS.get(model, model)
    body = {"model_path": path, "force_reload": True}
    variant = variant or (config.CHAT_GGUF_VARIANT
                          if path == config.CHAT_MODEL else None)
    if variant:
        # the backend defaults this repo to UD-Q4_K_XL (not cached -> slow
        # download); pin the cached quant via its gguf_variant field
        body["gguf_variant"] = variant
    if max_seq_length is not None:
        body["max_seq_length"] = max_seq_length
    elif path == config.CHAT_MODEL and config.CHAT_MAX_SEQ_LENGTH:
        body["max_seq_length"] = config.CHAT_MAX_SEQ_LENGTH
    elif path == config.MODEL and config.OCR_MAX_SEQ_LENGTH:
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
        assert sent["data"]["gguf_variant"] == "UD-Q6_K_XL", \
            "chat load must pin the cached GGUF variant"
        # literal path: posted verbatim, no max_seq_length (backend default)
        load("some/local/model-GGUF")
        assert sent["data"]["model_path"] == "some/local/model-GGUF"
        assert sent["data"]["force_reload"]
        assert "max_seq_length" not in sent["data"], \
            "non-default paths get no max_seq_length"
        assert "gguf_variant" not in sent["data"], \
            "non-chat-config paths get no gguf_variant"
        # explicit variant wins over the config pin, even for a literal path
        load("some/local/model-GGUF", variant="Q4_K_M")
        assert sent["data"]["gguf_variant"] == "Q4_K_M"
        # explicit max_seq_length wins over the per-role config default
        # (the generation profile threads context_length through the app)
        load("chat", max_seq_length=4096)
        assert sent["data"]["max_seq_length"] == 4096
        assert sent["data"]["gguf_variant"] == "UD-Q6_K_XL", \
            "variant pin still applies when a profile supplies max_seq_length"
        # config-MODEL path still gets its role's length override
        if config.OCR_MAX_SEQ_LENGTH:
            load(config.MODEL)
            assert sent["data"]["max_seq_length"] == config.OCR_MAX_SEQ_LENGTH
        # list_models: cached-gguf is the source of truth; one entry per
        # installed quant parsed from the snapshot filenames
        import tempfile
        from pathlib import Path as _P
        calls = []
        tmp = tempfile.mkdtemp(prefix="models_selftest_")
        gran = _P(tmp) / "granite" / "snapshots" / "s1"
        gran.mkdir(parents=True)
        (gran / "granite-4.1-8b-UD-Q6_K_XL.gguf").touch()
        glm_dir = _P(tmp) / "glm" / "snapshots" / "s1"
        glm_dir.mkdir(parents=True)
        (glm_dir / "GLM-OCR-Q8_0.gguf").touch()
        (glm_dir / "GLM-OCR-f16.gguf").touch()
        (glm_dir / "mmproj-GLM-OCR-Q8_0.gguf").touch()  # vision proj: skip
        partial = _P(tmp) / "partial" / "snapshots" / "s1"
        partial.mkdir(parents=True)
        (partial / "x-GGUF-incomplete.gguf").touch()

        def fake_gguf(method, path, data=None):
            calls.append(path)
            return {"cached": [
                {"repo_id": "unsloth/granite-4.1-8b-GGUF",
                 "cache_path": str(_P(tmp) / "granite")},
                {"repo_id": "ggml-org/GLM-OCR-GGUF",
                 "cache_path": str(_P(tmp) / "glm")},
                {"repo_id": "someone/Other-GGUF",
                 "cache_path": str(_P(tmp) / "partial")},
            ]}
        globals()["_api"] = fake_gguf
        got = list_models()
        assert calls == ["/api/models/cached-gguf"], calls
        assert {"path": "unsloth/granite-4.1-8b-GGUF", "name": "granite-4.1-8b (UD-Q6_K_XL)", "variant": "UD-Q6_K_XL"} in got
        assert {"path": "ggml-org/GLM-OCR-GGUF", "name": "GLM-OCR (Q8_0)", "variant": "Q8_0"} in got
        assert {"path": "ggml-org/GLM-OCR-GGUF", "name": "GLM-OCR (f16)", "variant": "f16"} in got
        assert all("mmproj" not in g["name"] for g in got), \
            "vision-projector mmproj files must be skipped"
        assert "someone/Other-GGUF" not in [g["path"] for g in got] or \
            {"path": "someone/Other-GGUF", "name": "Other (x-GGUF-incomplete)", "variant": "x-GGUF-incomplete"} in got
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
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