# VoxBridge local plugin package

This directory contains the local `0.2.0` developer-alpha plugin candidate and its voice-generation skill instructions. Users can choose immediate playback, a named binary audio-file resource, or both; compatible hosts can present the resource as a download. The registered private plugin remains on `0.1.0` until the gateway is verified through a real provider and Secure MCP Tunnel connection. This package does not contain the gateway, provider credentials, tunnel configuration, or a public MCP connection.

Raw PCM is playback-only in this developer alpha. File or combined delivery uses a self-describing format such as WAV, MP3, FLAC, AAC, Ogg/Opus, M4A, or WebM as supported by the selected provider.

## Current boundary

- `plugin.json`, `.codex-plugin/plugin.json`, and `skills/voice-generation/SKILL.md` are implemented in this repository.
- No `mcp.json` is included because the repository does not establish a stable deployed HTTPS endpoint.
- No live provider, tunnel, OAuth flow, or public deployment is implied or verified by this package.

For private development, start the gateway using its [private setup instructions](../voxbridge-gateway/README.md), then configure the developer client for local stdio, loopback HTTP, or a separately managed Secure MCP Tunnel as appropriate. Keep provider keys in the gateway environment; never put them in this plugin directory or type them into chat.

For a private developer-mode connection, create the tunnel in OpenAI Platform, run `tunnel-client` beside the gateway, and select **Tunnel** while connecting the app in ChatGPT. Use the tunnel ID through that supported connection flow; do not put the OpenAI-hosted tunnel endpoint into `mcp.json` as a normal server URL.

Secure MCP Tunnel is not a public-release transport. A public release instead needs stable HTTPS, OAuth, per-user credential isolation, and completed provider-policy review. Never publish a placeholder or unverified URL.
