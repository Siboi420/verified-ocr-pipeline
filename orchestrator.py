#!/usr/bin/env python3
"""RAG + tool-calling Q&A loop over the "Verified OCR" knowledge base.

Question -> (optional) hybrid RAG retrieval (top_k=3) -> chat loop with
ibm-granite/granite-4.2-8b-GGUF (config.CHAT_MODEL) + the 3 beam tool schemas.
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

import models  # noqa: E402 — sibling module, same pattern

import profiles  # noqa: E402 — sibling module, same pattern; pyright: ignore[reportMissingImports]

API_BASE = os.environ.get("UNSLOTH_API_BASE", config.API_BASE)
MODEL = config.CHAT_MODEL


def _loaded_model():
    """The model the backend currently has loaded (it rejects any other name
    in the payload — "Switch model by request" is off). Falls back to the
    config default when the status query fails or nothing is loaded."""
    try:
        return models.current_model() or MODEL
    except RuntimeError:
        return MODEL

DEFAULT_KB_NAME = "Verified OCR"  # resolved by name at runtime (KBs can be renamed/deleted)
TOP_K = 3
MAX_ITERS = 8
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
    "basis - do not include a chain-of-thought / reasoning preamble. Call a "
    "tool only when a number needs computing; once all needed tool results "
    "are back, stop and write the final answer immediately - do not call "
    "further tools, re-run a tool, or repeat yourself. When the "
    "user asks to break down, explain, or verify a previous tool result, re-run "
    "the tool with the same inputs instead of recomputing from memory. 'Ast', "
    "'minimum shear reinforcement', and 'minimum stirrup area' all mean Av,min - "
    "answer them with the min_shear_reinf tool."
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


def chat(messages, tools, max_tokens=None, thinking=False):
    """One /v1/chat/completions round-trip against the loaded model's
    generation profile. max_tokens None -> the resolved profile's cap
    (profiles.DEFAULTS fallback). Sampling params that resolve to None
    (top_k/top_p/min_p unless set) are omitted from the payload so they
    stay off. enable_thinking is sent EXPLICITLY every request: the GGUF
    backend loads with thinking on (Studio-managed), and an explicit
    False is the only way to run the fast path per query."""
    model = _loaded_model()
    p = profiles.resolve(model)
    body = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "max_tokens": max_tokens or p["max_tokens"],
        "enable_thinking": thinking,
    }
    for k in ("temperature", "top_k", "top_p", "min_p", "repeat_penalty"):
        if p[k] is not None:
            body[k] = p[k]
    return _api("POST", "/v1/chat/completions", body)["choices"][0]["message"]


REPEAT_GUARD_THRESHOLD = 5  # consecutive identical non-trivial lines -> truncate
REPEAT_LINE_MIN = 40         # ignore short lines (headers, bullet markers)
REPEAT_TRUNC_NOTE = "\n\n…(output truncated: repeated-line block)"


def _truncate_repetition(text):
    """Cut a degenerate repeated-line block (a known 8B-model failure mode
    that burns the whole token budget) so it never reaches the UI/session.
    Keeps the sane prefix before the first run of REPEAT_GUARD_THRESHOLD
    identical non-trivial lines, appends a truncation note."""
    lines = text.splitlines()
    for i in range(len(lines) - REPEAT_GUARD_THRESHOLD + 1):
        mid = lines[i]
        if len(mid.strip()) < REPEAT_LINE_MIN:
            continue
        if all(lines[i + k] == mid for k in range(REPEAT_GUARD_THRESHOLD)):
            prefix = "\n".join(lines[:i]).rstrip()
            return prefix + REPEAT_TRUNC_NOTE if prefix else REPEAT_TRUNC_NOTE
    return text


def run_loop(messages, tools, max_tokens, thinking=False):
    """Drive the chat/tool loop; returns (answer, trace). trace is an ordered
    list of {"kind": ...} steps: "reasoning" (reasoning_content, when the
    backend emits it), "message" (assistant content), "tool_call"
    (name + raw arguments), "tool_result" (the wrapper result or {error}),
    and a final "answer" step. Raises RuntimeError at the iteration cap.
    thinking threads into every chat() call (per-request enable_thinking)."""
    trace = []
    for _ in range(MAX_ITERS):
        message = chat(messages, tools, max_tokens, thinking)
        reasoning = message.get("reasoning_content")
        if reasoning:
            trace.append({"kind": "reasoning", "content": reasoning})
        try:
            mode, calls = extract_tool_calls(message)
        except ValueError as e:  # noqa: B015 — pi-lens-ignore: no-boolean-in-except (false positive: plain single-class except)
            # Malformed tool-call block: tell the model, keep the loop alive.
            # Note: avoid boolean ops in this body — the ast-grep rule above
            # scans the whole except subtree incl. the body (stopBy: end).
            messages.append(assistant_payload(message, "marker"))
            messages.append(tool_result("marker", None, {"error": str(e)}))
            trace.append({"kind": "message", "content": message.get("content", "")})
            trace.append({"kind": "tool_result", "result": {"error": str(e)}})
            continue
        trace.append({"kind": "message", "content": message.get("content") or ""})
        if not calls:
            answer = _truncate_repetition(message.get("content") or "")
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


# Ambiguity keywords: questions phrased as design/judgment calls get the
# thinking pass; straightforward parameter extraction runs the fast path.
# ponytail: fixed keyword set, not a real classifier — if the heuristic
# misfires, the fallback retry below still catches the fatal cases (a true
# classifier is only worth it when echo-confirmed misses are frequent).
_AMBIGUOUS_RE = re.compile(
    r"\b(?:should|recommend|dimension|assume|if|or)\b",
    re.IGNORECASE)


# Calculation-style questions must end in a tool call; a fast pass that
# returns prose without one is suspect -> escalate.
_CALC_STYLE_RE = re.compile(
    r"\b(?:capacity|shear|moment|flex|Ast|Av|min_shear|reinf|kN)\b",
    re.IGNORECASE)


def _is_ambiguous(question):
    """Design/judgment questions need thinking; straightforward parameter
    extraction doesn't. Small keyword heuristic — see _AMBIGUOUS_RE note."""
    return bool(_AMBIGUOUS_RE.search(question))


def _question_messages(user_turn, history, context):
    """system + history + the user turn with the retrieved context (fresh
    list per call, so the retry restarts from the seed, not the failed
    loop's appended messages)."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        if m.get("role") in ("user", "assistant"):
            messages.append({"role": m["role"], "content": m.get("content") or ""})
    messages.append({
        "role": "user",
        "content": f"{user_turn}\n\nContext:\n{context}",
    })
    return messages


def _wants_retry(answer, steps, question):
    """A non-thinking pass failed if the answer is empty, a tool call
    errored (malformed args surfaced back to the model), or a
    calculation-style question returned no tool call — escalate once."""
    if not answer.strip():
        return True
    if any(
        t["kind"] == "tool_result" and isinstance(t.get("result"), dict)
        and t["result"].get("error")
        for t in steps
    ):
        return True
    if _CALC_STYLE_RE.search(question) and not any(t["kind"] == "tool_call" for t in steps):
        return True
    return False


def answer_turn(user_turn, history, kb_id, max_tokens=None):
    """One user turn in a session: (retrieval?) + chat loop.

    history: prior messages [{"role": "user"|"assistant", "content": …}].
    kb_id: RAG KB to retrieve from; None = no retrieval (bare tools only).
    max_tokens: explicit cap; None falls back to the resolved profile's
    max_tokens (set in chat()).

    Thinking policy (per-query enable_thinking): ambiguous/judgment
    questions think on the first pass; straightforward parameter extraction
    runs the fast non-thinking path, escalating to thinking ONCE only when
    the fast pass fails (empty answer, tool-arg error, or a calc question
    answered without tools).
    Returns (answer, trace); the trace's first step (when kb_id is set) is
    {"kind": "retrieval", "chunks": […]}."""
    trace = []
    context = "(no retrieved chunks)"
    if kb_id:
        chunks = retrieve(user_turn, kb_id)
        trace.append({"kind": "retrieval", "chunks": chunks})
        context = "\n\n".join(chunks) if chunks else context
    thinking = _is_ambiguous(user_turn)
    answer, steps = run_loop(
        _question_messages(user_turn, history, context), load_tools(), max_tokens, thinking)
    if not thinking and _wants_retry(answer, steps, user_turn):
        # one-shot escalation: the fast path failed, try once with thinking
        answer, steps = run_loop(
            _question_messages(user_turn, history, context), load_tools(), max_tokens, True)
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
    assert names == ["design_beam", "flex_capacity", "min_shear_reinf", "shear_capacity"], names
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

    def fake_chat(messages, tools, max_tokens, thinking=False):
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

    # chat payload carries the resolved profile (defaults when no settings
    # on disk; the selftest stubs load_settings so a user's real
    # settings.json can never break it), plus explicit param keys for every
    # set value — repeat_penalty verified accepted live 2026-09-03 (HTTP
    # 200; fallback would be frequency_penalty 0.5)
    sent = {}
    saved_api = globals()["_api"]
    saved_load_settings = profiles.load_settings

    def fake_api(method, path, data=None):
        sent["data"] = data
        return {"choices": [{"message": {"content": "ok"}}]}

    globals()["_api"] = fake_api
    try:
        profiles.load_settings = lambda: {}
        chat([{"role": "user", "content": "q"}], load_tools(), 12000)
    finally:
        globals()["_api"] = saved_api
        profiles.load_settings = saved_load_settings
    # defaults flow into the payload; unset sampling params stay absent;
    # the fast path is the default (enable_thinking explicitly False)
    d = sent["data"]
    assert d["temperature"] == 0.2, d
    assert d["repeat_penalty"] == 1.1, d
    assert d["max_tokens"] == 12000, d
    assert d["enable_thinking"] == False, "default chat() must send enable_thinking=false"  # noqa: E712
    assert "top_k" not in d and "top_p" not in d and "min_p" not in d, \
        "unset sampling params must be omitted, not sent as null"

    # a profile override flows into the payload. chat() resolves the profile
    # under the LOADED model (which may be gemma/anything on a live box), so
    # pin _loaded_model to the override's key: otherwise the test decides by
    # whatever happens to be resident (verified flake 2026-10-08: gemma was
    # loaded, the granite override silently didn't apply, KeyError 'min_p').
    sent = {}
    saved_api = globals()["_api"]
    saved_load_settings = profiles.load_settings
    saved_loaded_model = globals().get("_loaded_model")

    def fake_api2(method, path, data=None):
        sent["data"] = data
        return {"choices": [{"message": {"content": "ok"}}]}

    globals()["_api"] = fake_api2
    try:
        globals()["_loaded_model"] = lambda: "ibm-granite/granite-4.2-8b-GGUF"
        profiles.load_settings = lambda: {
            "global": {"temperature": 0.7, "top_k": 33},
            "models": {"ibm-granite/granite-4.2-8b-GGUF": {"min_p": 0.05}},
        }
        chat([{"role": "user", "content": "q"}], load_tools(), None)
    finally:
        globals()["_api"] = saved_api
        profiles.load_settings = saved_load_settings
        if saved_loaded_model is None:
            globals().pop("_loaded_model", None)
        else:
            globals()["_loaded_model"] = saved_loaded_model
    d = sent["data"]
    assert d["temperature"] == 0.7 and d["top_k"] == 33, d
    assert d["min_p"] == 0.05, d
    assert d["repeat_penalty"] == 1.1, "unset repeat_penalty keeps the default"
    assert d["max_tokens"] == 12000, "None max_tokens -> profile default"

    # explicit thinking=True flows into the payload too
    saved_api = globals()["_api"]
    sent = {}

    def fake_api3(method, path, data=None):
        sent["data"] = data
        return {"choices": [{"message": {"content": "ok"}}]}

    globals()["_api"] = fake_api3
    try:
        chat([{"role": "user", "content": "q"}], load_tools(), 12000, thinking=True)
    finally:
        globals()["_api"] = saved_api
    assert sent["data"]["enable_thinking"] == True, sent["data"]  # noqa: E712

    # answer_turn routing/retry decisions (run_loop stubbed):
    #  - parameter-extraction style -> fast first pass, escalate to thinking
    #    once on failure (empty answer)
    #  - ambiguous keyword -> thinking on the first pass, no retry
    #  - calc-style no-keyword -> fast, retried because it answered without
    #    a tool call
    saved_run_loop = globals()["run_loop"]
    seen = []

    def fake_run_loop(messages, tools, max_tokens, thinking=False,
                      answer="") -> tuple:
        seen.append(thinking)
        if answer:
            return answer, [{"kind": "answer", "content": answer}]
        return "", [{"kind": "answer", "content": ""}]

    globals()["run_loop"] = fake_run_loop
    try:
        answer_turn("what is Av,min for b_w=350, f_c=28, f_yt=420?", [], None)
        answer_turn("which design should I assume for this beam?", [], None)
        answer_turn("calculate Vc for the beam", [], None)
    finally:
        globals()["run_loop"] = saved_run_loop
    # extraction question: fast (False) then escalate (True)
    assert seen[:2] == [False, True], f"fast-then-escalate expected, got {seen[:2]}"
    # ambiguous design question: thinking on first pass, no retry
    assert seen[2:3] == [True], f"ambiguous routes to thinking first pass, got {seen[2:3]}"
    # calc-style with no keyword: fast (False) then retry (True)
    assert seen[3:5] == [False, True], f"calc retry expected, got {seen[3:5]}"

    # repetition guard: a degenerate repeated-line block is truncated
    # (the 8B-model failure mode that burned the whole token budget in
    # session c1f3db7e07a0 — 343 identical lines)
    long_line = "x" * 60
    repeated = "\n".join([long_line] * 8)
    out = _truncate_repetition("prefix\n" + repeated)
    assert out == "prefix" + REPEAT_TRUNC_NOTE, out
    assert _truncate_repetition("clean text") == "clean text"
    assert _truncate_repetition("\n".join([long_line] * 3)) == "\n".join([long_line] * 3)
    assert _truncate_repetition("\n".join(["short"] * 8)) == "\n".join(["short"] * 8)

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