#!/usr/bin/env python3
"""RAG + tool-calling Q&A loop over the "Verified OCR" knowledge base.

Question -> (optional) hybrid RAG retrieval (top_k=3) -> chat loop with
unsloth/granite-4.1-8b-GGUF (config.CHAT_MODEL) + the 3 beam tool schemas.
When the model emits tool calls they are executed via
functions/wrapper.py:call_tool() and the results (or validation errors) are
fed back; the loop ends when the model answers without tool calls, or after
MAX_ITERS iterations. No HTTP server — the Flask app on :5000 owns the chat
UI and calls answer_turn().

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

# Load sibling module explicitly — works from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent / "functions"))
wrapper = importlib.import_module("wrapper")

import config  # noqa: E402 — after the sys.path juggling

API_BASE = os.environ.get("UNSLOTH_API_BASE", config.API_BASE)
MODEL = config.CHAT_MODEL
DEFAULT_KB_NAME = "Verified OCR"  # resolved by name at runtime (KBs can be renamed/deleted)
TOP_K = 3
MAX_ITERS = 8
TEMPERATURE = 0.2
# Cap output so a reasoning model can't spend the whole window on
# chain-of-thought and stop with an empty reply. Real answers need ~1-2k;
# 12000 leaves headroom while bounding worst-case CoT.  # ponytail: fixed cap,
# revisit if a legit answer ever needs >12k output tokens.

# From docs/infrastructure.md "Guardrails".
SYSTEM_PROMPT = (
    "You answer structural engineering questions using the retrieved context "
    "and the available calculation tools. No arithmetic from memory - always "
    "call the tool for any calculation. Cite every source from the context "
    "when you use it. Flag uncertainty if the input is ambiguous. No "
    "engineering judgment - redirect to an engineer. No data fabrication - "
    "if something is not in your sources, say so. Sections sized as \"200x300\" "
    "are b x h. Capacity tools take effective depth d, or h plus `cover_cg` "
    "(d = h - cover_cg); never pass total height h as d. After the tool calls "
    "return their results, answer the user directly with the final value and its "
    "basis - do not include a chain-of-thought / reasoning preamble."
)

# Qwen marker-format tool call: <tool_call>{"name": ..., "arguments": {...}}</tool_call>
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _api(method, path, data=None):
    """POST/GET JSON to Unsloth Studio; returns parsed body, raises RuntimeError
    with the HTTP status and body on failure (mirrors rag_uploader._api pattern)."""
    if not os.environ.get("UNSLOTH_API_KEY"):
        raise RuntimeError("UNSLOTH_API_KEY is not set (source .env.local)")
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


def default_kb_id():
    """Resolve DEFAULT_KB_NAME to its KB id at runtime; create it if missing
    (KBs can be renamed/deleted, so a cached id would strand the CLI)."""
    body = _api("GET", "/api/rag/knowledge-bases")
    for kb in body.get("knowledgeBases", []):
        if kb["name"] == DEFAULT_KB_NAME:
            return kb["id"]
    kb = _api("POST", "/api/rag/knowledge-bases",
              {"name": DEFAULT_KB_NAME,
               "description": "Verified OCR exports from seismic-ai-tools"})
    return kb["id"]


def retrieve(query, kb_id):
    """Hybrid RAG search; returns the chunk texts (empty list if none)."""
    body = _api("POST", "/api/rag/search",
                {"query": query, "kb_id": kb_id, "mode": "hybrid", "top_k": TOP_K})
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


def run_loop(messages, tools, max_tokens):
    """Drive the chat/tool loop; returns (answer, trace). trace is an ordered
    list of {"kind": ...} steps: "reasoning" (reasoning_content, when the
    backend emits it), "message" (assistant content), "tool_call"
    (name + raw arguments), "tool_result" (the wrapper result or {error}),
    and a final "answer" step. Raises RuntimeError at the iteration cap."""
    trace = []
    for _ in range(MAX_ITERS):
        message = chat(messages, tools, max_tokens)
        reasoning = message.get("reasoning_content")
        if reasoning:
            trace.append({"kind": "reasoning", "content": reasoning})
        try:
            mode, calls = extract_tool_calls(message)
        except ValueError as e:  # noqa: B015 — pi-lens-ignore: no-boolean-in-except (false positive: plain single-class except)
            # Malformed tool-call block: tell the model, keep the loop alive.
            messages.append(assistant_payload(message, "marker"))
            messages.append(tool_result("marker", None, {"error": str(e)}))
            trace.append({"kind": "message", "content": message.get("content") or ""})
            trace.append({"kind": "tool_result", "result": {"error": str(e)}})
            continue
        trace.append({"kind": "message", "content": message.get("content") or ""})
        if not calls:
            answer = message.get("content") or ""
            trace.append({"kind": "answer", "content": answer})
            return answer, trace
        messages.append(assistant_payload(message, mode))
        for call_id, name, raw_arguments in calls:
            trace.append({"kind": "tool_call", "name": name,
                          "arguments": raw_arguments})
            try:
                result = wrapper.call_tool(name, **_norm_args(raw_arguments))
            except (ValueError, json.JSONDecodeError, TypeError) as e:
                result = {"error": str(e)}
            trace.append({"kind": "tool_result", "result": result})
            messages.append(tool_result(mode, call_id, result))
    raise RuntimeError("reached iteration cap without a final answer")


def answer_turn(user_turn, history, kb_id, max_tokens):
    """One user turn in a session: (retrieval?) + chat loop.

    history: prior messages [{"role": "user"|"assistant", "content": …}].
    kb_id: RAG KB to retrieve from; None = no retrieval (bare tools only).
    Returns (answer, trace); the trace's first step (when kb_id is set) is
    {"kind": "retrieval", "chunks": […]}."""
    trace = []
    context = "(no retrieved chunks)"
    if kb_id:
        chunks = retrieve(user_turn, kb_id)
        trace.append({"kind": "retrieval", "chunks": chunks})
        context = "\n\n".join(chunks) if chunks else context
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        if m.get("role") in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m.get("content") or ""})
    messages.append({
        "role": "user",
        "content": f"{user_turn}\n\nContext:\n{context}",
    })
    answer, steps = run_loop(messages, load_tools(), max_tokens)
    return answer, trace + steps


def answer_question(question, max_tokens):
    """CLI one-shot: answer a question against the default KB; returns text.
    Raises RuntimeError at the iteration cap and on API failures."""
    answer, _ = answer_turn(question, [], default_kb_id(), max_tokens)
    return answer


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

    # run_loop trace shapes + tool execution, with chat() stubbed (no server/key)
    saved_chat = globals()["chat"]
    sequence = [
        {"content": "", "reasoning_content": "think", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "min_shear_reinf",
                          "arguments": '{"b_w": 350, "f_c": 28, "f_yt": 420}'}}]},
        {"content": "final answer text", "reasoning_content": "thought again"},
    ]
    idx = {"n": 0}

    def fake_chat(messages, tools, max_tokens):
        msg = sequence[idx["n"]]
        idx["n"] += 1
        return msg

    globals()["chat"] = fake_chat
    try:
        answer, trace = run_loop(
            [{"role": "user", "content": "q"}], load_tools(), 12000)
    finally:
        globals()["chat"] = saved_chat
    assert answer == "final answer text", answer
    kinds = [t["kind"] for t in trace]
    assert kinds == ["reasoning", "message", "tool_call", "tool_result",
                     "reasoning", "message", "answer"], kinds
    tcall = trace[2]
    assert tcall["name"] == "min_shear_reinf" and "b_w" in json.dumps(tcall["arguments"]), tcall
    tresult = trace[3]
    assert abs(tresult["result"]["value"] - 291.67) < 0.01, tresult  # real wrapper call
    assert trace[-1]["kind"] == "answer" and trace[-1]["content"] == answer

    print("PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", help="question to ask (default: read from stdin)")
    parser.add_argument("--max-tokens", type=int, default=12000,
                        help="chat completion token cap (default 12000; bounds reasoning so it can't burn the whole window and stop empty)")
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
        print(answer_question(question, args.max_tokens))
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())