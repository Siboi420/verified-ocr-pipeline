# Voice / AIRI roadmap (not implemented — documentation only)

Goal: ask the verified ACI KB by voice. No voice code ships this round; this
doc is the target architecture and the planned API surface, so a later
implementation has a fixed contract to build against.

## Pipeline

```text
browser mic → STT → session store → assistant engine → TTS → playback
```

1. **Capture**: browser `MediaRecorder` → webm/opus blob (client-side; existing
   chat UI gains a mic button).
2. **STT**: local `RealtimeSTT` service (whisper-based). Planned route:
   `POST /api/voice/stt` with `{"audio": "<base64>"}` → `{"text": "…"}`.
3. **Session store**: existing `sessions/<id>.json` — the transcript is the
   same `messages` array; a voice turn just appends a user message.
4. **Assistant engine**: existing `orchestrator.answer_turn()` (RAG top_k=3 +
   the 3 beam tools); nothing new on the model side.
5. **TTS**: `alltalk_tts`. Planned route: `POST /api/voice/tts` with
   `{"text": "…"}` → `{"audio": "<base64>"}` (played via `<audio>`).

The planned endpoint contract (`POST /api/voice/stt`, `POST /api/voice/tts`) is
stable now even though the routes don't exist yet — a later implementation
should not rename them.

## Alternative backend

Unsloth Studio (`:8888`) already exposes audio endpoints that could serve both
stages, avoiding two extra services:
- STT: `POST /api/inference/audio/stt/load` (model load),
  `GET /api/inference/audio/stt/status`, `POST /api/inference/audio/transcribe`,
  `POST /api/inference/audio/transcriptions`.
- TTS-ish: `POST /api/inference/audio/speech`, `POST /api/inference/audio/generate`
  (+ `POST /api/inference/audio/gallery` listing).

If those prove good enough, the planned `/api/voice/*` routes become thin
proxies. RealtimeSTT/alltalk remain the primary reference for quality.

## AIRI reference

AIRI (moeru-ai, a multimodal speech-agent project) is used as the
orchestration reference for how STT → LLM → TTS are looped and streamed —
adopt its *pattern* (turn framing, mic/speaker lifecycle, latency budget),
not its code. "Adjusted to AI assistant" = ours is a keyboard-first tool with
an optional voice channel; AIRI's always-on full-duplex is out of scope.

## Out of scope

- Streaming request/response audio, push-to-talk UX details, wake words,
  speaker diarization, offline ASR model choice.
- No new Python deps this round; nothing in `requirements-ocr.txt` changes.