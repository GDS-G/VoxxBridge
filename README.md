# VoxBridge

VoxBridge is an AI audio plugin and MCP gateway for provider-neutral speech plus ElevenLabs music generation. This repository contains the `0.4.0` private gateway and plugin candidate, together with a private hosted-gateway image.

## Repository layout

- [`voxbridge-gateway`](voxbridge-gateway/README.md): Python MCP server, eight speech-provider adapters, ElevenLabs Music support, tests, local Docker packaging, and deployment guidance.
- [`voxbridge-plugin`](voxbridge-plugin/README.md): plugin manifest plus separate voice- and music-generation skills.
- [`Dockerfile`](Dockerfile): private hosted image that combines VoxBridge with the official OpenAI Secure MCP Tunnel client.
- [`third_party/openai-tunnel-client`](third_party/openai-tunnel-client/README.md): pinned upstream license and attribution for the bundled tunnel-client binary.
- [GitHub workflows](.github/workflows): test/build CI and multi-platform GHCR publishing.

## Current release boundary

The gateway is implemented and verified locally with mocked provider contracts, current and legacy MCP clients, a real loopback Streamable HTTP smoke test, lint, and package builds. Speech can be returned for immediate playback, as a short-lived named audio-file resource, or both from one provider generation. Ordered segments using different voice IDs can also be synthesized sequentially and assembled into one PCM WAV file. `generate_music` adds ElevenLabs prompt- or composition-plan-based MP3 generation, using `music_v2_5` by default, with the same `playback`, `file`, and `both` delivery choices. File-capable results include an MCP App that uses the host's native Download flow when available and offers a user-initiated **Add file to ChatGPT** action. That action materializes the already-generated file, verifies its exact bytes, and then passes the preserved embedded resource through `ui/update-model-context` to a compatible ChatGPT composer; the host may request approval. Materialization alone does not create an attachment. The bounded materialization tool accepts either the exact generated materialization URI or, when a host hides that URI, the exact generated filename; it does not make another provider call. Short-window, session-scoped replay suppression prevents approval retries from repeating provider work. Composer handoffs do not promise reusable-library persistence or that every editing tool accepts arbitrary audio. The hosted image keeps the MCP listener on container loopback and uses an outbound-only Secure MCP Tunnel; it does not expose provider-funded tools on a public unauthenticated URL.

Live use requires an OpenAI tunnel ID, a tunnel runtime key, and at least one provider credential. Credential-free CI cannot establish account entitlements, vendor availability, licensing, or audio quality, so each deployment still needs a low-cost live acceptance check. When validating ElevenLabs Music intentionally, make one 3-second MP3 with `delivery="file"` and confirm the returned resource is readable; avoid repeated billable samples. Keep all secrets in the hosting platform's sealed variables—never in this repository or chat.

GitHub Pages cannot execute the Python gateway. GitHub hosts the source, CI, release assets, and `ghcr.io/gds-g/voxbridge-gateway`; an always-on compute service runs the image. The recommended private-alpha host is Railway with one service and no public domain. See the [deployment guide](voxbridge-gateway/DEPLOYMENT.md).
