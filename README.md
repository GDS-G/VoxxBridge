# VoxBridge

VoxBridge is a provider-neutral voice-generation plugin and MCP gateway. This repository contains hardened `0.2.0` gateway and plugin candidates plus a private hosted-gateway image.

## Repository layout

- [`voxbridge-gateway`](voxbridge-gateway/README.md): Python MCP server, eight provider adapters, tests, local Docker packaging, and deployment guidance.
- [`voxbridge-plugin`](voxbridge-plugin/README.md): plugin manifest and voice-generation skill.
- [`Dockerfile`](Dockerfile): private hosted image that combines VoxBridge with the official OpenAI Secure MCP Tunnel client.
- [`third_party/openai-tunnel-client`](third_party/openai-tunnel-client/README.md): pinned upstream license and attribution for the bundled tunnel-client binary.
- [GitHub workflows](.github/workflows): test/build CI and multi-platform GHCR publishing.

## Current release boundary

The gateway is implemented and verified locally with mocked provider contracts, current and legacy MCP clients, a real loopback Streamable HTTP smoke test, lint, and package builds. The hosted image keeps the MCP listener on container loopback and uses an outbound-only Secure MCP Tunnel; it does not expose provider-funded tools on a public unauthenticated URL.

Live use still requires an OpenAI tunnel ID, a tunnel runtime key, and at least one provider credential. No provider credential was available in this workspace, so paid synthesis and audio quality are not yet verified. Keep all secrets in the hosting platform's sealed variables—never in this repository or chat.

GitHub Pages cannot execute the Python gateway. GitHub hosts the source, CI, release assets, and `ghcr.io/gds-g/voxbridge-gateway`; an always-on compute service runs the image. The recommended private-alpha host is Railway with one service and no public domain. See the [deployment guide](voxbridge-gateway/DEPLOYMENT.md).
