# VoxBridge

VoxBridge is a provider-neutral voice-generation plugin and MCP gateway. This repository contains a hardened `0.3.0` gateway, the `0.3.2` private plugin candidate, and a private hosted-gateway image.

## Repository layout

- [`voxbridge-gateway`](voxbridge-gateway/README.md): Python MCP server, eight provider adapters, tests, local Docker packaging, and deployment guidance.
- [`voxbridge-plugin`](voxbridge-plugin/README.md): plugin manifest and voice-generation skill.
- [`Dockerfile`](Dockerfile): private hosted image that combines VoxBridge with the official OpenAI Secure MCP Tunnel client.
- [`third_party/openai-tunnel-client`](third_party/openai-tunnel-client/README.md): pinned upstream license and attribution for the bundled tunnel-client binary.
- [GitHub workflows](.github/workflows): test/build CI and multi-platform GHCR publishing.

## Current release boundary

The gateway is implemented and verified locally with mocked provider contracts, current and legacy MCP clients, a real loopback Streamable HTTP smoke test, lint, and package builds. Speech can be returned for immediate playback, as a short-lived named audio-file resource, or both from one provider generation. Ordered segments using different voice IDs can also be synthesized sequentially and assembled into one PCM WAV file. File-capable results include an MCP App that can send the actual bytes through the host download flow, request ChatGPT file-library storage with a session-upload fallback, and retain the underlying resource link for non-UI clients. A bounded materialization tool accepts either the exact generated materialization URI or, when a host hides that URI, the exact generated filename. Short-window, session-scoped replay suppression prevents approval retries from repeating provider work. These handoffs do not promise that every ChatGPT editing tool accepts arbitrary audio attachments. The hosted image keeps the MCP listener on container loopback and uses an outbound-only Secure MCP Tunnel; it does not expose provider-funded tools on a public unauthenticated URL.

Live use requires an OpenAI tunnel ID, a tunnel runtime key, and at least one provider credential. Credential-free CI cannot establish account entitlements, vendor availability, or audio quality, so each deployment still needs a low-cost live acceptance check. Keep all secrets in the hosting platform's sealed variables—never in this repository or chat.

GitHub Pages cannot execute the Python gateway. GitHub hosts the source, CI, release assets, and `ghcr.io/gds-g/voxbridge-gateway`; an always-on compute service runs the image. The recommended private-alpha host is Railway with one service and no public domain. See the [deployment guide](voxbridge-gateway/DEPLOYMENT.md).
