# VoxBridge local plugin package

This directory contains the private `0.3.1` developer-alpha plugin candidate and its voice-generation skill instructions. Users can explicitly choose immediate playback, a downloadable audio file, or both, and can combine ordered turns from several voice IDs into one PCM WAV file. The gateway's MCP App presents file bytes through a host-mediated Download button. When ChatGPT exposes its reusable file library, the App offers **Save to ChatGPT**; when only session upload is available, it offers **Upload to ChatGPT** without claiming library persistence. Non-UI MCP clients retain standard resource access, and compatible downstream tools can request bounded embedded bytes by passing the exact `materialize_resource_uri` from the generation result. Saving, uploading, or materializing a file does not guarantee that every ChatGPT editing tool accepts arbitrary audio input. This package binds the user-owned private VoxBridge Gateway app; it does not contain the gateway, provider credentials, tunnel configuration, or a public MCP connection.

Raw headerless PCM is unavailable in this developer alpha because it lacks portable sample metadata. Use a self-describing format such as WAV, MP3, FLAC, AAC, Ogg/Opus, M4A, or WebM as supported by the selected provider.

## Current boundary

- `plugin.json`, `.codex-plugin/plugin.json`, `.app.json`, and `skills/voice-generation/SKILL.md` are implemented in this repository.
- `.app.json` declares the existing private VoxBridge Gateway app as a required dependency. The app identifier is not a credential; provider and tunnel secrets remain sealed outside the package.
- No `mcp.json` is included because the repository does not establish a stable deployed HTTPS endpoint.
- Provider credentials, tunnel configuration, OAuth, and hosting remain outside this package.

For private development, start the gateway using its [private setup instructions](../voxbridge-gateway/README.md). The required private app must remain connected to that gateway through its separately managed Secure MCP Tunnel. Keep provider keys in the gateway environment; never put them in this plugin directory or type them into chat.

For a private developer-mode connection, create the tunnel in OpenAI Platform, run `tunnel-client` beside the gateway, and select **Tunnel** while connecting the app in ChatGPT. Use the tunnel ID through that supported connection flow; do not put the OpenAI-hosted tunnel endpoint into `mcp.json` as a normal server URL.

Secure MCP Tunnel and this private app binding are not public-release transports. A public OpenAI-directory package must omit `.app.json` and both manifest app declarations, and instead needs stable HTTPS, OAuth, per-user credential isolation, and completed provider-policy review. Never publish a placeholder or unverified URL.
