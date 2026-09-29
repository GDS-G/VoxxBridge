---
name: voice-generation
description: Generate realistic speech and compare voices across VoxBridge providers. Use when the user asks for TTS, narration, character dialogue, voice comparison, voice selection, or provider-specific voice controls.
---

# VoxBridge voice generation

VoxBridge is provider-neutral. Never assume a specific fictional universe, character set, business, or content workflow.

When the VoxBridge MCP gateway is connected:

1. Call `list_providers` when provider availability or capabilities are unknown.
2. Call `list_voices` before generation when the user has not provided an exact voice ID.
3. Call `generate_speech` using the user's requested provider, `voice_id`, language, model, `output_format`, speed, and performance direction.
4. Map natural-language delivery direction into `instructions` only when `list_providers` reports support. Hume currently requires `model="octave-1"` for acting direction; OpenAI excludes `tts-1` and `tts-1-hd`. Respect each provider's `instructions_note`.
5. Use `options_json` only for documented provider-specific controls. Do not invent parameters.
6. Respect the selected provider's reported `speed_range`, formats, character limit, allowed options, and `control_notes`.
7. Return generated audio directly when the tool returns audio content and identify it as synthetic AI-generated audio.
8. If a provider is not configured, state that clearly and offer another connected provider rather than silently switching.
9. If the user asks to compare providers, keep the source text and requested performance direction as consistent as each provider permits.
10. VoxBridge currently exposes text-to-speech and voice listing only. Do not claim voice cloning or speech-to-speech is available.

Initial providers: ElevenLabs, Hume AI, Cartesia, Resemble AI, OpenAI, Deepgram, Google Cloud Text-to-Speech, and Microsoft Azure Speech.
