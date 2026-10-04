# VoxBridge local plugin package

This directory contains the private `0.5.0` developer-alpha plugin candidate with separate voice-, music-, and sound-effect-generation skill instructions. Users can choose immediate playback, a downloadable audio file, or both for every generation type.

Speech is available through eight gateway adapters: ElevenLabs, Hume, Cartesia, Resemble, OpenAI, Deepgram, Google Cloud Text-to-Speech, and Microsoft Azure Speech. Multi-voice dialogue is not ElevenLabs-only: it can combine ordered turns from several voice IDs through any configured speech adapter that reports `supports_dialogue: true`. All eight current speech adapters advertise that capability, subject to every segment returning compatible PCM WAV parameters. One dialogue file still uses one provider and one global model; cross-provider dialogue is not supported.

Music is available through ElevenLabs Music, Google Cloud Lyria 2, and Stability AI Stable Audio 2.5. Sound effects are available through ElevenLabs Sound Effects and Stability AI Stable Audio 2.5. The plugin instructions always consult `list_providers`, use only a configured adapter advertising the requested capability, and never silently substitute providers because controls, cost, duration, output formats, entitlements, and terms differ.

The gateway's MCP App presents a native Download button only when the host advertises `ui/download-file`. Its separate **Add file to ChatGPT** action calls the app-visible `materialize_audio_file` tool with the exact existing filename, verifies the returned bytes, and then passes the preserved embedded resource through `ui/update-model-context` to a compatible ChatGPT composer. It reports success only after that context update succeeds and never regenerates audio. Host approval may be required. ChatGPT and compatible downstream tools can also request the same bounded embedded bytes by passing the exact returned `materialize_resource_uri` or, when the host hides that custom URI, the exact generated `file_name`. Materialization by itself does not create an attachment, and composer attachment does not promise reusable-library persistence, a reusable file ID, or acceptance by every editing tool. This package binds the user-owned private VoxBridge Gateway app; it does not contain the gateway, provider credentials, tunnel configuration, or a public MCP connection.

Raw headerless PCM is unavailable in this developer alpha because it lacks portable sample metadata. Use a self-describing format such as WAV, MP3, FLAC, AAC, Ogg/Opus, M4A, or WebM as supported by the selected provider.

## Current boundary

- `plugin.json`, `.codex-plugin/plugin.json`, `.app.json`, `skills/voice-generation/SKILL.md`, `skills/music-generation/SKILL.md`, and `skills/sound-effect-generation/SKILL.md` are implemented in this repository.
- `.app.json` declares the existing private VoxBridge Gateway app as a required dependency. The app identifier is not a credential; provider and tunnel secrets remain sealed outside the package.
- No `mcp.json` is included because the repository does not establish a stable deployed HTTPS endpoint.
- Provider credentials, tunnel configuration, OAuth, and hosting remain outside this package.

The music skill exposes new-generation prompt workflows for all three music providers and composition plans for ElevenLabs only. It does not expose audio-reference or music upload, inpainting, video-to-music, stem separation, finetune creation, or post-generation section editing. The sound-effect skill exposes text-prompt generation, not audio-to-audio editing. Music and sound-effect calls can consume paid quota or credits and can require account-specific entitlements. VoxBridge does not grant or promise copyright ownership, royalty-free status, uniqueness, or commercial clearance; users must review the selected provider's current terms, their plan, applicable law, and their rights in prompts, lyrics, samples, finetune data, and outputs.

For private development, start the gateway using its [private setup instructions](../voxbridge-gateway/README.md). The required private app must remain connected to that gateway through its separately managed Secure MCP Tunnel. Keep provider keys and Google credentials in the gateway environment or hosting platform's sealed secret store; never put them in this plugin directory, plugin ZIP, image, source control, or chat. Google Cloud Lyria must be enabled independently from Google Cloud Text-to-Speech and requires a project plus current Vertex AI access.

For a private developer-mode connection, create the tunnel in OpenAI Platform, run `tunnel-client` beside the gateway, and select **Tunnel** while connecting the app in ChatGPT. Use the tunnel ID through that supported connection flow; do not put the OpenAI-hosted tunnel endpoint into `mcp.json` as a normal server URL.

Secure MCP Tunnel and this private app binding are not public-release transports. A public OpenAI-directory package must omit `.app.json` and both manifest app declarations, and instead needs stable HTTPS, OAuth, per-user credential isolation, and completed provider-policy review. Never publish a placeholder or unverified URL.
