---
name: music-generation
description: Generate, play, download, or attach original music through VoxBridge using ElevenLabs Music, Google Cloud Lyria, or Stability AI Stable Audio. Use when the user asks for a song, instrumental, soundtrack, musical cue, composition plan, or downloadable AI-generated music file.
---

# VoxBridge music generation

Use this workflow for new synthetic music. It is separate from speech synthesis, multi-voice dialogue, and short sound effects.

When the VoxBridge MCP gateway is connected:

1. Call `list_providers` before generation unless the current provider state is already known. Use only a configured provider reporting `music_generation`. Never silently substitute providers; their models, cost, duration, formats, licensing terms, and controls differ.
2. Call `generate_music` exactly once for the requested track. Generation can consume paid quota. Do not create speculative samples, variations, or retries the user did not request.
3. Map delivery explicitly: `playback` for listening only, `file` for download only, and `both` when both are requested. When unspecified, use `both`.
4. Omit `model` and `output_format` to use the selected provider's advertised defaults. If specifying either, use values returned by `list_providers`.
5. Do not regenerate merely to change presentation. For a local save, use the MCP App Download control. For a conversation attachment or downstream byte access, use `materialize_audio_file` with the exact returned `materialize_resource_uri`, or the exact returned `file_name` only when the host hides that URI. The App verifies and attaches those same bytes without another provider call.
6. If generation succeeds but delivery fails, explain that provider charges may already apply. Do not retry without the user's direction.

## ElevenLabs Music

- Use `provider="elevenlabs"`. The default model is `music_v2_5`; `music_v1` and `music_v2` are available when specifically needed. Output is MP3.
- Provide exactly one of `prompt` or `composition_plan_json`. ElevenLabs prompts accept up to 4,100 characters. Composition plans must be complete non-empty JSON objects serialized as strings.
- Prompt duration accepts 3,000–600,000 milliseconds and defaults to a bounded 30,000 milliseconds. `force_instrumental` and duration are prompt-only.
- `seed` is composition-plan-only and accepts 0–2,147,483,647. `finetune_id`, section-duration enforcement, and MP3 C2PA signing are ElevenLabs-specific.
- Do not pass `negative_prompt`; ElevenLabs does not expose it as a separate control.

## Google Cloud Lyria 2

- Use `provider="google-lyria"`, model `lyria-002`, and WAV output. The provider must be explicitly enabled with a Google Cloud project and appropriate Vertex AI access.
- Use one US-English `prompt`; optional `negative_prompt` describes elements to exclude. `seed` is allowed. Do not pass a composition plan, duration, finetune, or C2PA control.
- Lyria 2 returns one provider-fixed instrumental 48 kHz WAV. Do not promise a user-selected length, vocals, lyrics, or a specific watermark state; VoxBridge does not expose a watermark control or assert watermark status in result metadata.

## Stability AI Stable Audio 2.5

- Use `provider="stability"`, model `stable-audio-2.5`, and MP3 or WAV. Prompts accept up to 10,000 characters.
- Duration accepts 1,000–190,000 milliseconds and defaults to a bounded 30,000 milliseconds. `seed` accepts 0–4,294,967,294. MP3 can use the full provider range; for WAV, obey `wav_duration_max_seconds_for_audio_limit` from `list_providers` because VoxBridge rejects a request that would exceed its configured byte cap before making a paid call.
- The API does not expose a separate `negative_prompt`, `force_instrumental`, composition plan, finetune, or C2PA control. Put desired vocals, instrumental-only intent, structure, mood, and exclusions directly in the prompt.

Treat every result as synthetic AI-generated music. Do not promise copyright ownership, royalty-free status, commercial clearance, or a particular license. The user remains responsible for the selected provider's current terms, account plan, applicable law, and rights in prompts, lyrics, samples, and outputs. Avoid requests that copy copyrighted lyrics or target a protected song, recording, or living artist too closely; offer a high-level genre, instrumentation, era, mood, and structure description instead.

This release generates new music from text or an ElevenLabs composition plan. It does not expose uploads, audio-reference transformation, inpainting, video-to-music, stem separation, finetune training, or post-generation editing. Do not imply those operations are available.
