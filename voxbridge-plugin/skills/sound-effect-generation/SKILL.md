---
name: sound-effect-generation
description: Generate, play, download, or attach sound effects, Foley, ambience, loops, one-shots, and short audio elements through VoxBridge using ElevenLabs or Stability AI. Use when the user requests a sound effect or non-speech audio asset rather than a full music track.
---

# VoxBridge sound-effect generation

When the VoxBridge MCP gateway is connected:

1. Call `list_providers` unless provider state is already known. Use only a configured provider reporting `sound_effect_generation`. Never silently substitute providers.
2. Call `generate_sound_effect` exactly once for the requested asset. Generation can consume paid quota, so do not create speculative variants or approval retries.
3. Write a clear prompt describing the event, materials, environment, perspective, intensity, timing, and whether the result should be a one-shot, ambience, Foley, transition, stem, or loop.
4. Map delivery explicitly: `playback` for listening only, `file` for download only, and `both` for both. When unspecified, use `both`.
5. Omit `model` and `output_format` to use provider defaults. Use the provider metadata for supported models, formats, duration limits, looping, and prompt-influence support.

## ElevenLabs Sound Effects

- Use `provider="elevenlabs"`. The default model is `eleven_text_to_sound_v2`; VoxBridge returns MP3.
- Prompts accept up to 450 characters. Duration is optional from 0.5–30 seconds; omission lets ElevenLabs choose. `loop=true` requests a seamless loop. `prompt_influence` accepts 0–1 and defaults to 0.3.
- Do not pass `seed`; ElevenLabs does not expose one for this endpoint.

## Stability AI Stable Audio 2.5

- Use `provider="stability"`. The default model is `stable-audio-2.5`; MP3 and WAV are available.
- Prompts accept up to 10,000 characters. Duration accepts 1–190 seconds and defaults to a bounded 5 seconds in VoxBridge. `seed` accepts 0–4,294,967,294. MP3 can use the full provider range; for WAV, obey `wav_duration_max_seconds_for_audio_limit` from `list_providers` so the request stays inside the configured byte cap.
- Do not pass `loop` or `prompt_influence`; Stable Audio 2.5 does not expose those controls. Describe repeatable texture or prompt adherence in plain language instead, without promising a technically seamless loop.

For download, use the MCP App's Download control. For attachment or downstream byte access, call `materialize_audio_file` with the exact returned `materialize_resource_uri`, or the exact returned `file_name` only when the host hides that URI. This uses the existing bytes and must not regenerate the effect. The host may request approval.

Identify the result as synthetic audio. Do not promise that it is royalty-free, commercially cleared, unique, or free of similarity to existing recordings. Follow the selected provider's current terms and content rules. If generation succeeds but delivery fails, state that charges may already apply and do not retry without direction.
