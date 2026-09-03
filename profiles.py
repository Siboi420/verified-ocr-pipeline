"""LLM generation profiles: one global set of defaults plus per-model
overrides, persisted in BASE/settings.json (gitignored) and applied to every
chat session automatically.

Resolution order for a model: built-in DEFAULTS <- global <- models[key].
Per-request params that resolve to None are omitted from the /v1/chat/
completions payload, so top_k/top_p/min_p stay off unless set. context_length
is load-time only (max_seq_length on the next model load), never a request
param. stdlib only.

    POST /api/settings {"global": {"temperature": 0.5}, "models": {...}}
    GET  /api/settings -> stored shape (empty when missing)

Usage: python3 profiles.py --selftest
"""
import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

# name -> (kind, built-in default). None default = unset/inherit.
_FIELDS = {
    "temperature": ("float", 0.2),
    "top_k": ("integer", None),
    "top_p": ("float", None),
    "min_p": ("float", None),
    "repeat_penalty": ("float", 1.1),
    "max_tokens": ("integer", 12000),
    "context_length": ("integer", None),  # load-time max_seq_length only
}
DEFAULTS = {name: default for name, (_, default) in _FIELDS.items()}


def _coerce(v, integer):
    """One param value -> number or None (unset); ValueError on garbage.
    '' / null -> None (inherit); numeric strings coerced; NaN/Inf and
    non-numeric values rejected."""
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        try:
            v = float(v)
        except ValueError:
            raise ValueError(f"not a number: {v!r}") from None
    elif v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"not a number: {v!r}")
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError(f"non-finite value: {v!r}")
    # v is now int or a finite float: math.trunc can't raise, no int()/float()
    # conversion of untrusted input here
    if integer:
        if isinstance(v, float) and not v.is_integer():
            raise ValueError(f"integer required: {v!r}")
        return math.trunc(v)
    return v


def sanitize(data):
    """Validate/coerce a raw POST shape -> {"global": {...}, "models": {...}}.
    Unknown keys are dropped; ''/null values are dropped (inherit semantics,
    so an empty global is a no-op); numeric garbage raises ValueError
    (NaN/Inf included, matching the bbox-coord guard). Per-model entries
    that sanitize to nothing are dropped (that's the "clear override"
    action)."""
    if not isinstance(data, dict):
        raise ValueError("settings must be an object")
    clean = {"global": {}, "models": {}}

    def _section(obj):
        out = {}
        if not isinstance(obj, dict):
            raise ValueError("section must be an object")
        for k, v in obj.items():
            if k not in _FIELDS:
                continue  # unknown keys dropped, never persisted
            v = _coerce(v, _FIELDS[k][0] == "integer")
            if v is not None:
                out[k] = v
        return out

    g = data.get("global")
    clean["global"] = _section({} if g is None else g)
    models_in = data.get("models")
    if models_in is None:
        models_in = {}
    if not isinstance(models_in, dict):
        raise ValueError("models must be an object")
    for key, vals in models_in.items():
        if not isinstance(key, str) or not key or not isinstance(vals, dict):
            continue  # structurally malformed entry: skip, don't persist
        entry = _section(vals)
        if entry:
            clean["models"][key] = entry
    return clean


def load_settings():
    """The stored settings as-is, or {} when missing/corrupt (never raise:
    a broken settings file must fall back to defaults, not 500 the app)."""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data):
    """sanitize + persist + return the clean settings."""
    clean = sanitize(data)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(clean, indent=2))
    return clean


def _match_model(model, models_map):
    """The per-model section for a model path, or {}: exact key match, or a
    key whose base name (trailing -GGUF stripped) appears inside the model
    path — so a resolved snapshot path like
    "granite-4.1-8b-UD-Q6_K_XL.gguf" still matches the repo-id key
    "unsloth/granite-4.1-8b-GGUF" (same fuzzy approach the model bar uses)."""
    if not models_map or not model:
        return {}
    if model in models_map:
        return models_map[model]
    for key, vals in models_map.items():
        base = key.rsplit("/", 1)[-1]
        if base.endswith("-GGUF"):
            base = base[:-5]
        if base and base in model:
            return vals
    return {}


def resolve(model):
    """Effective params for a model: built-in defaults <- global <-
    models[key] (per-model wins). Every key of DEFAULTS is present; None =
    omit from the request payload."""
    out = dict(DEFAULTS)
    settings = load_settings()
    for section in (settings.get("global") or {},
                    _match_model(model, settings.get("models") or {})):
        for k, v in section.items():
            if k in out and v is not None:
                out[k] = v
    return out


def selftest():
    # isolate from any real settings.json on disk — never let a user's
    # saved profile break the offline checks
    tmp = tempfile.mkdtemp(prefix="profiles_selftest_")
    saved_path = SETTINGS_PATH
    try:
        globals()["SETTINGS_PATH"] = Path(tmp) / "settings.json"
        assert load_settings() == {}, "missing file -> {}"

        # built-in defaults
        assert DEFAULTS == {"temperature": 0.2, "top_k": None, "top_p": None,
                            "min_p": None, "repeat_penalty": 1.1,
                            "max_tokens": 12000, "context_length": None}, DEFAULTS
        # unknown top-level keys dropped
        save_settings({"global": {"temperature": 0.5, "bogus": 1}})
        assert load_settings() == {"global": {"temperature": 0.5},
                                   "models": {}}, load_settings()
        # per-model override beats built-ins; global fields still inherited
        save_settings({"models": {"unsloth/granite-4.1-8b-GGUF":
                                  {"top_k": 40, "context_length": 32768}}})
        got = resolve("unsloth/granite-4.1-8b-GGUF")
        assert got["top_k"] == 40 and got["context_length"] == 32768, got
        assert got["temperature"] == 0.2 and got["repeat_penalty"] == 1.1, got
        # global merges under per-model; resolved snapshot path matches the
        # repo-id key by base name
        save_settings({"global": {"temperature": 0.5},
                       "models": {"unsloth/granite-4.1-8b-GGUF": {"top_k": 40}}})
        got = resolve("granite-4.1-8b-UD-Q6_K_XL.gguf")
        assert got["temperature"] == 0.5 and got["top_k"] == 40, got
        # no matching key -> global only
        got = resolve("ggml-org/GLM-OCR-GGUF")
        assert got["top_k"] is None and got["temperature"] == 0.5, got

        # numeric coercion + empty -> drop (inherit); unknown keys dropped
        clean = sanitize({"global": {"temperature": "0.3", "top_k": "40",
                                     "max_tokens": ""},
                          "models": {"m1": {"min_p": ""},
                                     "m2": {"repeat_penalty": 1.3}}})
        assert clean == {"global": {"temperature": 0.3, "top_k": 40},
                         "models": {"m2": {"repeat_penalty": 1.3}}}, clean
        assert sanitize({"bogus": 1, "global": {"nope": 2}})["global"] == {}
        # NaN / Inf / garbage / fractional int -> ValueError
        for bad in (float("nan"), float("inf"), "abc", True):
            try:
                sanitize({"global": {"temperature": bad}})
                assert False, f"expected ValueError for {bad!r}"
            except ValueError:
                pass
        try:
            sanitize({"global": {"top_k": 1.5}})
            assert False, "fractional int must be rejected"
        except ValueError:
            pass
        # corrupt file -> defaults, never raise
        (SETTINGS_PATH).write_text("{not json")
        assert load_settings() == {}
    finally:
        globals()["SETTINGS_PATH"] = saved_path
        shutil.rmtree(tmp, ignore_errors=True)
    print("profiles: selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true",
                    help="offline checks, no server")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    print("no CLI action (library only; the app owns settings.json); "
          "try --selftest", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())