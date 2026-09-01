#!/usr/bin/env python3
"""RAG + tool-calling Q&A loop over the "Verified OCR" knowledge base.

Question -> hybrid RAG retrieval (top_k=3) -> chat loop with
unsloth/Qwen3.8-27B-GGUF + the 3 beam tool schemas. When the model emits
tool calls they are executed via functions/wrapper.py:call_tool() and the
results (or validation errors) are fed back; the loop ends when the model
answers without tool calls, or after MAX_ITERS iterations.

Usage:
    python3 orchestrator.py --question "What is Av,min for b_w=350 f'c=28?"
    echo "..." | python3 orchestrator.py
    python3 orchestrator.py --selftest
"""
import argparse
import importlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Load sibling module explicitly — works from any cwd; pyright stays silent
# (same pattern as functions/test_shear_tools.py)
sys.path.insert(0, str(Path(__file__).resolve().parent / "functions"))
wrapper = importlib.import_module("wrapper")

API_BASE = os.environ.get("UNSLOTH_API_BASE", "http://localhost:8888")
MODEL = "unsloth/Qwen3.8-27B-GGUF"
KB_ID = "24895fae-4771-4381-b7e8-75c4ee7b5bae"
TOP_K = 3
MAX_ITERS = 8
TEMPERATURE = 0.2

# From docs/infrastructure.md "Guardrails" (lines ~89-95); the "exact" prompt
# text referenced in the plan was not available, this is the documented source.
SYSTEM_PROMPT = (
    "You answer structural engineering questions using the retrieved context "
    "and the available calculation tools. No arithmetic from memory - always "
    "call the tool for any calculation. Cite every source from the context "
    "when you use it. Flag uncertainty if the input is ambiguous. No "
    "engineering judgment - redirect to an engineer. No data fabrication - "
    "if something is not in your sources, say so."
)

# Qwen marker-format tool call: <tool_call>{"name": ..., "arguments": {...}}</tool_call>
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _api(method, path, data=None):
    """POST/GET JSON to Unsloth Studio; returns parsed body, raises RuntimeError
    with the HTTP status and body on failure (mirrors rag_uploader._api pattern)."""
    req = urllib.request.Request(
        API_BASE + path,
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['UNSLOTH_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"HTTP {e.code} {method} {path}: {e.read().decode(errors='replace')[:500]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"{method} {path} failed: {e}") from e


def load_tools():
    """schema/*.json -> OpenAI function-calling tool definitions."""
    tools = []
    for path in sorted(Path(__file__).resolve().parent.glob("schemas/*.json")):
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"cannot read tool schema {path}: {e}") from e
        tools.append({
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        })
    return tools


def retrieve(query):
    """Hybrid RAG search; returns the chunk texts (empty list if none)."""
    body = _api("POST", "/api/rag/search",
                {"query": query, "kb_id": KB_ID, "mode": "hybrid", "top_k": TOP_K})
    return [r["text"] for r in body.get("results", [])]


def _norm_args(arguments):
    """Tool arguments as a dict; raises ValueError on malformed JSON so the
    caller can feed the error back to the model instead of crashing."""
    if isinstance(arguments, str):
        try:
            return json.loads(arguments or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid tool arguments JSON: {arguments[:200]!r}") from e
    return arguments or {}


def extract_tool_calls(message):
    """(mode, calls) where mode is "native"|"marker"|None and calls is
    [(call_id, name, raw_arguments), ...] with raw_arguments a JSON string or
    dict (parsed later by _norm_args, inside the caller's error handling).
    Native reads message.tool_calls (OpenAI shape); marker scans content for
    <tool_call> blocks (Qwen)."""
    calls = message.get("tool_calls") or []
    if calls:
        return "native", [(c["id"], c["function"]["name"], c["function"]["arguments"])
                          for c in calls]
    blocks = _TOOL_CALL_RE.findall(message.get("content") or "")
    if not blocks:
        return None, []
    parsed = []
    for b in blocks:
        try:
            obj = json.loads(b)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid <tool_call> JSON: {b[:200]!r}") from e
        parsed.append((None, obj["name"], obj.get("arguments")))
    return "marker", parsed


def assistant_payload(message, mode):
    """Echo the assistant message back (reasoning_content dropped - not part
    of the OpenAI schema and not needed for the round-trip)."""
    if mode == "native":
        return {"role": "assistant", "content": message.get("content") or "",
                "tool_calls": message["tool_calls"]}
    return {"role": "assistant", "content": message["content"]}


def tool_result(mode, call_id, result):
    content = json.dumps(result)
    if mode == "native":
        return {"role": "tool", "tool_call_id": call_id, "content": content}
    return {"role": "tool", "content": f"<tool_response>\n{content}\n</tool_response>"}


def chat(messages, tools, max_tokens):
    return _api("POST", "/v1/chat/completions", {
        "model": MODEL,
        "messages": messages,
        "tools": tools,
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
    })["choices"][0]["message"]


def run(question, max_tokens):
    """Answer one question; returns the process exit code."""
    chunks = retrieve(question)
    context = "\n\n".join(chunks) if chunks else "(no retrieved chunks)"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": f"{question}\n\nContext:\n{context}"},
    ]
    tools = load_tools()

    for _ in range(MAX_ITERS):
        message = chat(messages, tools, max_tokens)
        try:
            mode, calls = extract_tool_calls(message)
        except ValueError as e:
            # Malformed tool-call block: tell the model, keep the loop alive.
            messages.append(assistant_payload(message, "marker"))
            messages.append(tool_result("marker", None, {"error": str(e)}))
            continue
        if not calls:
            print(message.get("content") or "")
            return 0
        messages.append(assistant_payload(message, mode))
        for call_id, name, raw_arguments in calls:
            try:
                result = wrapper.call_tool(name, **_norm_args(raw_arguments))
            except (ValueError, json.JSONDecodeError, TypeError) as e:
                result = {"error": str(e)}
            messages.append(tool_result(mode, call_id, result))
    print("error: reached iteration cap without a final answer", file=sys.stderr)
    return 1


def selftest():
    """Offline checks (no server, no API key needed)."""
    tools = load_tools()
    names = [t["function"]["name"] for t in tools]
    assert names == ["flex_capacity", "min_shear_reinf", "shear_capacity"], names
    for t in tools:
        assert t["type"] == "function", t
        assert set(t["function"]) == {"name", "description", "parameters"}, t["function"]
        assert t["function"]["parameters"]["type"] == "object", t["function"]

    native = {"content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "min_shear_reinf",
                      "arguments": '{"b_w": 350, "f_c": 28, "f_yt": 420}'}}]}
    mode, calls = extract_tool_calls(native)
    assert mode == "native" and len(calls) == 1, f"mode={mode} calls={calls}"
    assert calls[0][0] == "call_1" and calls[0][1] == "min_shear_reinf", calls[0]
    assert _norm_args(calls[0][2]) == {"b_w": 350, "f_c": 28, "f_yt": 420}, calls[0]

    marker = {"content": 'Some text\n\n<tool_call>\n{"name": "flex_capacity", '
                         '"arguments": {"b": 300, "d": 500, "A_s": 1200, "f_c": 28, "f_yl": 420}}\n'
                         '</tool_call>\ndone'}
    mode, calls = extract_tool_calls(marker)
    assert mode == "marker" and len(calls) == 1, f"mode={mode} calls={calls}"
    assert calls[0][1] == "flex_capacity", calls[0]
    assert _norm_args(calls[0][2])["b"] == 300, calls[0]

    plain = {"content": "no tools here"}
    mode, calls = extract_tool_calls(plain)
    assert mode is None and calls == [], f"mode={mode} calls={calls}"

    # Round-trip shapes
    assert assistant_payload(native, "native")["tool_calls"] == native["tool_calls"]
    tr = tool_result("marker", None, {"value": 1.0})
    assert tr["content"].startswith("<tool_response>") and tr["content"].endswith("</tool_response>")

    print("PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", help="question to ask (default: read from stdin)")
    parser.add_argument("--max-tokens", type=int, default=32000,
                        help="chat completion token cap (default 32000; the model burns tokens on reasoning)")
    parser.add_argument("--selftest", action="store_true", help="run offline checks and exit")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return 0

    if not os.environ.get("UNSLOTH_API_KEY"):
        print("error: UNSLOTH_API_KEY is not set (source .env.local)", file=sys.stderr)
        return 1

    question = args.question or sys.stdin.read().strip()
    if not question:
        parser.print_usage(sys.stderr)
        print("error: no question (pass --question or pipe stdin)", file=sys.stderr)
        return 2

    try:
        return run(question, args.max_tokens)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())