# VoxBridge Gateway

VoxBridge `0.5.0` is an MCP gateway for provider-neutral realistic speech, multi-voice dialogue, music, and sound-effect generation. It exposes one text-to-speech surface for eight providers:

- ElevenLabs
- Hume AI
- Cartesia
- Resemble AI
- OpenAI
- Deepgram
- Google Cloud Text-to-Speech
- Microsoft Azure Speech

> **Developer alpha:** “implemented” below means the code path exists in this repository. It does not mean a public service is live, a provider integration has been exercised against a real account, or the provider has reviewed or approved this project.

## Status

| Area | Repository status |
| --- | --- |
| `list_providers`, `list_voices`, `generate_speech`, `generate_dialogue`, `generate_music`, `generate_sound_effect`, and `materialize_audio_file` | Implemented |
| Speech providers | Eight adapters: ElevenLabs, Hume, Cartesia, Resemble, OpenAI, Deepgram, Google Cloud Text-to-Speech, and Microsoft Azure Speech |
| Music providers | ElevenLabs Music, Google Cloud Lyria 2, and Stability AI Stable Audio 2.5 |
| Sound-effect providers | ElevenLabs Sound Effects and Stability AI Stable Audio 2.5 |
| stdio and Streamable HTTP transports | Implemented |
| Loopback-by-default HTTP and an explicit non-loopback safety gate | Implemented |
| Unit tests, lint, MCP smoke test, and package-build workflow | Configured for Python 3.11 and 3.12; a workflow run is the evidence that a particular revision passed |
| Live provider calls | Require the relevant credentials and account access; not established by the repository or credential-free CI |
| Secure MCP Tunnel | A pinned hosted image and supervisor are included; tunnel/runtime-key provisioning still happens in OpenAI Platform |
| Hosted public endpoint, OAuth, and per-user credential storage | Not implemented or deployed by this repository |

The plugin package deliberately contains no provider keys and no placeholder public MCP URL.

## MCP tools

- `list_providers()` returns capabilities, defaults, supported formats, `supports_dialogue`, and whether each adapter is configured. Provider IDs are `elevenlabs`, `hume`, `cartesia`, `resemble`, `openai`, `deepgram`, `google`, `azure`, `google-lyria`, and `stability`.
- `list_voices(provider, language?, limit?)` returns a provider voice catalog.
- `generate_speech(..., delivery="playback" | "file" | "both")` invokes the selected provider once and returns the selected representation: playable MCP `AudioContent`, a short-lived named `ResourceLink` with a host-mediated Download button, or both via app-only playback data plus the same downloadable resource. The default is `both`.
- `generate_dialogue(provider, segments, model?, language?, delivery?)` synthesizes ordered segments with a different `voice_id` per segment and assembles them into one PCM WAV file. All eight speech adapters currently report `supports_dialogue: true`. The current release keeps one provider and one global model per file, makes one upstream call per segment, and rejects incompatible returned WAV parameters rather than silently resampling.
- `generate_music(prompt?, composition_plan_json?, negative_prompt?, music_length_ms?, model?, output_format?, force_instrumental?, seed?, finetune_id?, respect_sections_durations?, sign_with_c2pa?, delivery="playback" | "file" | "both", provider="elevenlabs")` creates one synthetic music track. Omitted model and format values resolve from the selected provider. ElevenLabs supports a prompt or composition plan and returns MP3; Google Lyria accepts a prompt and returns a provider-fixed instrumental WAV; Stability accepts a prompt and returns MP3 or WAV.
- `generate_sound_effect(prompt, duration_seconds?, loop?, prompt_influence?, seed?, model?, output_format?, delivery="playback" | "file" | "both", provider="elevenlabs")` creates one sound effect, ambience, Foley event, one-shot, loop, or short musical component. ElevenLabs returns MP3 and supports its loop and prompt-influence controls; Stability returns MP3 or WAV and supports a seed.
- `materialize_audio_file(resource_uri?, file_name?)` returns generated speech, dialogue, music, or sound effects as a bounded embedded binary resource when ChatGPT or another compatible downstream tool needs the exact bytes. Pass exactly one locator: prefer the exact compact `materialize_resource_uri` included in every file-bearing generation result, or use the exact generated `file_name` when a host hides that custom URI. The longer ResourceLink `resource_uri` remains accepted for compatibility. Never infer or alter either locator. The tool prepares the embedded resource; it does not attach the file by itself. The MCP App separately uses `ui/update-model-context` to add the verified resource to a compatible ChatGPT composer. Neither operation promises a reusable-library file ID or universal downstream audio support.

ChatGPT can replay a non-idempotent tool call while resolving an approval step. VoxBridge hashes the normalized request, scopes it to the ChatGPT session when that metadata is available, and reuses a successful identical result for up to two minutes. Concurrent duplicates are single-flighted, failed calls are never cached, and at most two completed results are retained (reduced further when configured artifact capacity requires it). Distinct in-flight work is also capped at the configured generation-concurrency limit. This guard prevents an approval replay from repeating provider charges for speech, dialogue, music, or sound effects; it does not change the tools' non-idempotent contract or authorize callers to submit deliberate duplicates.

`playback` returns inline MCP `AudioContent`. `file` returns a short-lived `ResourceLink`. `both` intentionally keeps public content in the downloadable `ResourceLink` shape and sends the same generated bytes to the bound MCP App through model-hidden tool-result metadata. This preserves the portable file reference while giving the App reliable playback; `resources/read` remains a fallback for MCP Apps hosts that do not forward custom metadata. Clients without MCP Apps still receive the readable file resource.

| `delivery` | Immediate playback | Downloadable file |
| --- | --- | --- |
| `playback` | Yes | No |
| `file` | No | Yes |
| `both` | Yes | Yes |

The Download button is an MCP App using the standard `ui/download-file` host flow and appears only when the host advertises that capability. The App first reads the short-lived private resource and forwards its existing base64 blob directly to the host, with the `ResourceLink` retained as a portable fallback, so no public file URL is required. Local download is distinct from conversation attachment. The separate **Add file to ChatGPT** action is shown only when the host advertises App tool calls plus OpenAI composer support for embedded resources. It invokes `materialize_audio_file` with the exact generated filename, verifies the returned bytes against the original size and SHA-256 metadata, preserves the complete `EmbeddedResource`, and then calls `ui/update-model-context`. Only after that context update succeeds does the App report that the file was added to the composer; the user sends the next message to share it with the model. No audio is regenerated. This bounded handoff uses the gateway's configured materialization limit (8 MiB by default); that cap is not a statement of ChatGPT's file policy. It does not promise reusable-library persistence, a reusable file ID, or acceptance by every editing tool. Clients without MCP Apps support still receive the named `ResourceLink` and can read it with standard MCP resource APIs. `VB-HANDOFF-MATERIALIZE` identifies a failed App handoff without exposing temporary URLs or opaque file identifiers.

### Multi-voice dialogue boundary

Each dialogue segment includes `text`, `voice_id`, optional `instructions`, `speed`, `language`, provider-specific `options`, and `pause_after_ms`. The final segment's pause is ignored. The developer-alpha defaults are at most 10 segments, 5,000 total characters, 30 seconds of inserted pauses, 600 seconds of combined audio, and 20 MiB of final audio.

Dialogue is not ElevenLabs-only. It works with a configured speech adapter that reports `supports_dialogue: true`; all eight speech adapters in this release currently do. Each file still uses one provider, one global model, and that provider's voice IDs. All deterministic shared validation runs before synthesis, then segments are generated sequentially and appended incrementally. A provider can still reject a later segment after earlier calls complete. On any segment, format, size, duration, or assembly failure, VoxBridge stores no combined file and reports that earlier provider charges may already apply. The current release deliberately supports compatible PCM WAV segments only. MP3 output, cross-provider files, per-segment models, and automatic resampling require a future decode/resample/encode pipeline.

### Music boundary

`generate_music` is deliberately separate from speech, dialogue, and sound effects. Select a configured provider that advertises `music_generation`; VoxBridge never silently switches providers. Omit `model` and `output_format` to use the selected provider's defaults.

- **ElevenLabs** (`provider="elevenlabs"`): accepts exactly one prompt (up to 4,100 characters) or one complete `composition_plan_json` object serialized as a string (up to 40,000 characters). The default model is `music_v2_5`; `music_v1` and `music_v2` remain available. Prompt-mode duration is 3,000–600,000 milliseconds and defaults to 30,000. `force_instrumental` and duration are prompt-only; `seed` is composition-plan-only. `finetune_id`, section-duration enforcement, and MP3 C2PA signing are provider-specific. Output is MP3.
- **Google Cloud Lyria 2** (`provider="google-lyria"`): accepts one US-English prompt, optional `negative_prompt`, and optional seed with model `lyria-002`. It returns one provider-fixed instrumental 48 kHz WAV; no caller-selected duration, vocals, composition plan, finetune, C2PA, or watermark control is exposed. This is a distinct provider from Google Cloud Text-to-Speech.
- **Stability AI Stable Audio 2.5** (`provider="stability"`): accepts a prompt up to 10,000 characters, optional seed, and a duration of 1,000–190,000 milliseconds, defaulting to 30,000. Model `stable-audio-2.5` returns MP3 or WAV. MP3 can use the full provider duration range; uncompressed WAV is preflighted against the gateway's configured audio-byte limit before any paid call, and `list_providers` reports the resulting `wav_duration_max_seconds_for_audio_limit` (about 59 seconds under the conservative default 20 MiB cap). Put instrumental/vocal intent and exclusions in the prompt; the API surface does not expose separate `negative_prompt`, `force_instrumental`, composition-plan, finetune, or C2PA controls.

Every generation can consume provider quota or credits. Approval-sensitive identical host replays use the same short-window safeguard as speech, but callers must not intentionally submit duplicates to obtain another presentation or attachment. If the provider completes generation but VoxBridge cannot store or deliver the result, the error warns that charges may already apply.

This release does not expose audio-reference or music upload, inpainting, video-to-music, stem separation, finetune creation/training, or post-generation section editing. Generated music is synthetic audio, not a promise of copyright ownership, royalty-free status, or commercial clearance. Users are responsible for the selected provider's current terms, account plan, applicable law, and rights in prompts, lyrics, samples, finetune data, and downstream use. Avoid copyrighted lyrics and prompts that target protected songs, recordings, or living artists too closely.

### Sound-effect boundary

`generate_sound_effect` uses a configured provider advertising `sound_effect_generation` and shares music's delivery, materialization, and replay safeguards.

- **ElevenLabs** (`provider="elevenlabs"`): model `eleven_text_to_sound_v2`, MP3 output, prompts up to 450 characters, optional 0.5–30-second duration, seamless-loop request, and `prompt_influence` from 0–1 (default 0.3). ElevenLabs does not expose a seed for this endpoint.
- **Stability AI** (`provider="stability"`): model `stable-audio-2.5`, MP3 or WAV, prompts up to 10,000 characters, 1–190-second duration (default 5 seconds in VoxBridge), and optional seed. MP3 can use the full provider duration range; WAV is preflighted against the configured audio-byte cap using the `wav_duration_max_seconds_for_audio_limit` provider metadata. This API surface does not expose seamless-loop or prompt-influence controls.

Sound effects are synthetic audio and can consume paid quota or credits. A generated effect is not a promise of uniqueness, copyright ownership, royalty-free status, or commercial clearance. Review the selected provider's current terms and content rules.

Provider-specific controls are capability-gated. VoxBridge does not silently switch providers.
`list_providers` reports both `supported_formats` and `file_delivery_formats`. Raw headerless PCM is not exposed in this developer alpha because it lacks portable sample metadata; request WAV instead.

## Private local setup

Run these commands from `voxbridge-gateway`.

```text
python -m venv .venv
```

Activate the environment and install the package with developer tools:

```powershell
# PowerShell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

```bash
# POSIX shell
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

Keep `.env` local, fill only the provider credentials you intend to use, and start the default Streamable HTTP server:

```text
voxbridge
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. Health endpoints are:

- `GET /healthz`: process health.
- `GET /readyz`: returns success only when at least one provider is configured; no configured providers produces `503` by design.

The server reads `.env` from its working directory. Do not commit `.env`, service-account files, generated credentials, or provider keys.

## Provider environment variables

Configure only providers you plan to use. Provider keys and Google credential files are runtime secrets; never place them in the plugin ZIP, image, repository, or chat.

| Provider | Capability | Required configuration |
| --- | --- | --- |
| ElevenLabs | Speech, dialogue, music, sound effects | `ELEVENLABS_API_KEY`; music and Sound Effects API access remain subject to the account's plan and entitlements. |
| Hume AI | Speech, dialogue | `HUME_API_KEY` |
| Cartesia | Speech, dialogue | `CARTESIA_API_KEY`; `CARTESIA_VERSION` is fixed to the supported `2026-08-14` contract. |
| Resemble AI | Speech, dialogue | `RESEMBLE_API_KEY` |
| OpenAI | Speech, dialogue | `OPENAI_API_KEY` |
| Deepgram | Speech, dialogue | `DEEPGRAM_API_KEY` |
| Google Cloud Text-to-Speech | Speech, dialogue | Application Default Credentials (ADC). For local file-based ADC, set `GOOGLE_APPLICATION_CREDENTIALS`. For metadata/workload-identity ADC, set `GOOGLE_CLOUD_TTS_ENABLED=true`. `GOOGLE_CLOUD_PROJECT` is an optional quota-project override. |
| Google Cloud Lyria 2 | Music | ADC plus a required `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_MUSIC_ENABLED=true`, and current Vertex AI API/IAM/model access. `GOOGLE_CLOUD_MUSIC_LOCATION` defaults to `global`. Enabling Google TTS does not enable Lyria, and enabling Lyria does not enable TTS. |
| Stability AI Stable Audio 2.5 | Music, sound effects | `STABILITY_API_KEY`; generation is credit-metered by Stability AI. |
| Microsoft Azure Speech | Speech, dialogue | `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` |

Gateway settings are also environment variables:

| Variable | Safe developer default | Purpose |
| --- | --- | --- |
| `VOXBRIDGE_TRANSPORT` | `streamable-http` | Select `streamable-http` or `stdio`. |
| `VOXBRIDGE_HOST` | `127.0.0.1` | HTTP listen address. |
| `VOXBRIDGE_PORT` | `8000` | HTTP listen port. |
| `VOXBRIDGE_ALLOW_REMOTE_BIND` | `false` | Explicitly acknowledges a non-loopback bind; it does not add authentication. |
| `VOXBRIDGE_LOG_LEVEL` | `INFO` | Server log level. |
| `VOXBRIDGE_MAX_TEXT_CHARS` | `3000` | Maximum input text size. |
| `VOXBRIDGE_MAX_AUDIO_BYTES` | `20971520` | Maximum returned audio size. |
| `VOXBRIDGE_MAX_CONCURRENT_GENERATIONS` | `2` | Process-local generation concurrency. |
| `VOXBRIDGE_MAX_DIALOGUE_SEGMENTS` | `10` | Maximum ordered voice turns in one combined file. |
| `VOXBRIDGE_MAX_DIALOGUE_CHARS` | `5000` | Maximum aggregate dialogue text. |
| `VOXBRIDGE_MAX_DIALOGUE_PAUSE_MS` | `30000` | Maximum aggregate inserted silence, excluding the ignored final pause. |
| `VOXBRIDGE_MAX_DIALOGUE_DURATION_SECONDS` | `600` | Maximum decoded combined WAV duration. |
| `VOXBRIDGE_MAX_MATERIALIZED_AUDIO_BYTES` | `8388608` | Maximum audio size returned inline by `materialize_audio_file`. |
| `VOXBRIDGE_AUDIO_DOWNLOAD_TTL_SECONDS` | `900` | Lifetime of a generated file resource in the process-local download cache. |
| `VOXBRIDGE_AUDIO_DOWNLOAD_MAX_ITEMS` | `32` | Maximum number of generated file resources retained by one replica. |
| `VOXBRIDGE_AUDIO_DOWNLOAD_MAX_BYTES` | `67108864` | Maximum total bytes retained for generated file downloads by one replica. |
| `VOXBRIDGE_REQUEST_TIMEOUT_SECONDS` | `60` | Outbound provider timeout. |
| `VOXBRIDGE_MUSIC_REQUEST_TIMEOUT_SECONDS` | `300` | Longer outbound timeout for music and Stability AI audio generation. |
| `VOXBRIDGE_SOUND_EFFECT_REQUEST_TIMEOUT_SECONDS` | `120` | Longer outbound timeout for ElevenLabs sound-effect generation. |
| `VOXBRIDGE_ALLOWED_HOSTS` | loopback hosts | JSON array of Host-header patterns accepted by the MCP transport. |
| `VOXBRIDGE_ALLOWED_ORIGINS` | loopback HTTP origins | JSON array of browser origins accepted by the MCP transport. |

## Transport choices

### stdio: the smallest private surface

Use stdio when the MCP client can launch the gateway as a local subprocess. No TCP listener is created.

```powershell
$env:VOXBRIDGE_TRANSPORT = "stdio"
voxbridge
```

```bash
VOXBRIDGE_TRANSPORT=stdio voxbridge
```

Pass provider variables through the process environment or the local `.env` file. Do not place secret values in a checked-in MCP configuration.

### Loopback HTTP

The default HTTP configuration binds only to `127.0.0.1`. The gateway refuses a non-loopback address unless `VOXBRIDGE_ALLOW_REMOTE_BIND=true`. Host and Origin checks remain enabled in every bind mode. If a private proxy uses a non-loopback Host header, add its exact host pattern and origin to `VOXBRIDGE_ALLOWED_HOSTS` and `VOXBRIDGE_ALLOWED_ORIGINS`; do not use broad wildcard domains. The remote-bind override is a safety acknowledgement, not access control, encryption, or authentication.

### Secure MCP Tunnel for private development

For a remote private developer client, run VoxBridge on loopback and point a separately managed Secure MCP Tunnel at `http://127.0.0.1:8000/mcp`. The tunnel supplies private reachability without a public gateway listener.

The repository-root [`Dockerfile`](../Dockerfile) packages this topology with the official OpenAI `tunnel-client` pinned to `v0.0.15`. `voxbridge-hosted` supervises both processes, forces the gateway onto loopback, and connects the tunnel client to the stateless Streamable HTTP endpoint. This HTTP boundary also tolerates a brief host deployment overlap; the unsupported multiple-stdio-child topology is not used.

The image still needs `CONTROL_PLANE_TUNNEL_ID`, `CONTROL_PLANE_API_KEY`, and at least one provider credential at runtime. Tunnel and key creation are intentionally not automated or stored in the image.

Only use a non-loopback gateway bind when another private sidecar or network topology truly requires it. Keep the listener unreachable from the public internet and set `VOXBRIDGE_ALLOW_REMOTE_BIND=true` only after that boundary is in place. The provided hosted image does not require this override.

## Docker loopback caveat

`127.0.0.1` inside a container is the container’s loopback interface. A gateway bound there is not reachable through a normal Docker published port. For host-only developer access, bind the process to all container interfaces but publish the port only on the host loopback interface:

Run this example from the repository root:

```bash
docker build -t voxbridge-gateway ./voxbridge-gateway
docker run --rm \
  --env-file ./voxbridge-gateway/.env \
  -e VOXBRIDGE_HOST=0.0.0.0 \
  -e VOXBRIDGE_ALLOW_REMOTE_BIND=true \
  -p 127.0.0.1:8000:8000 \
  voxbridge-gateway
```

The `0.0.0.0` bind exists only inside the container in this example; the host publishes it on `127.0.0.1`. Do not change the publish address to a public interface for this developer-alpha workflow. A container-to-container tunnel needs an equivalently private network boundary.

## Provider-control notes

- Speed is validated against the selected vendor's current range, which is returned by `list_providers`.
- ElevenLabs rejects explicit language selection on `eleven_multilingual_v2`; `eleven_v3` rejects speed, similarity boost, and speaker boost controls that it does not support. These model caveats are returned in `control_notes`.
- ElevenLabs Music and Sound Effects API use can require a paid plan or account entitlement. Both are billable and are separate from speech and voice cloning.
- Google Cloud Lyria is a separate, explicitly enabled music adapter. It requires a project with current Vertex AI access and returns instrumental WAV audio; Google Cloud Text-to-Speech remains a separate provider ID. Watermarking is provider-managed and VoxBridge does not expose a watermark control or assert watermark status in result metadata.
- Stability AI Stable Audio is a separate music/sound-effect adapter and does not provide speech or dialogue. Successful generations consume Stability credits under the provider's current pricing.
- Hume Octave 2 requires a saved voice. Hume delivery instructions currently require `model="octave-1"`; Octave 2 requests with instructions fail before any billable call. Hume states that its TTS and EVI APIs will shut down on November 13, 2026, so plan migration rather than treating this adapter as a long-term dependency.
- Cartesia emotion values and volume are validated against the current `2026-08-14` contract; emotion is rejected for an explicitly non-English language.

## Checks

```text
python -m ruff format --check .
python -m ruff check .
python -m pytest
python -m build
```

To exercise a running Streamable HTTP server end to end without spending provider credits:

```text
python scripts/smoke_mcp.py
```

The MCP App source lives under `web/`; `npm ci` followed by `npm run build` regenerates the single-file HTML bundled into the Python package. Lint, unit tests, and successful wheel/source and UI builds validate repository mechanics. They do not validate provider credentials, live vendor APIs, account entitlements, content-policy compliance, or audio quality.

Live provider checks are intentionally outside credential-free CI. With the account owner's action-time authorization, exercise only the configured capabilities: one short speech sample, one two-segment dialogue for a provider reporting `supports_dialogue`, and the shortest practical music or sound-effect sample needed for each paid integration. Use `delivery="file"`, read the returned resource, and confirm non-empty bytes with the expected MIME type. Reuse that exact result for playback, download, attachment, and replay checks instead of regenerating it. Do not treat one provider's success as acceptance of another provider, and do not run speculative samples merely because credentials are present.

Private-alpha downloads use opaque, short-lived MCP resource links backed by a bounded in-memory cache on the single gateway replica. The MCP App requests a host-mediated file save only when `ui/download-file` is advertised, so downloads need no public file URL. To preserve reliable in-widget playback, `both` includes a base64 copy of the generated bytes in model-hidden App metadata; this adds roughly one-third encoding overhead to the initial result. `materialize_audio_file` is separately capped because embedded tool results are expensive and powers the first stage of the user-initiated **Add file to ChatGPT** action; the verified resource is attached through `ui/update-model-context` in the second stage. The host may request approval. Links expire, and a deployment restart invalidates them. Longer production audio should use an app-only streaming helper or authenticated durable storage with short-lived downloads and lifecycle deletion.

## Before a public launch

A public release needs a stable HTTPS MCP URL and OAuth between the MCP client and VoxBridge. Environment-global provider credentials must be replaced by encrypted, revocable, per-user credential storage with appropriate metering, rate limits, audit events, and retention controls.

Each provider’s current terms, acceptable-use rules, voice-cloning or impersonation requirements, consent requirements, and disclosure obligations must also be reviewed before enabling that provider publicly. Do not describe the service as public, production-ready, provider-approved, or live until those controls are implemented and independently verified.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the release boundary and connection example.

Official references: [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [plugin authentication](https://developers.openai.com/plugins/build/auth), and [building an MCP server](https://developers.openai.com/plugins/build/mcp-server).
