# VoxBridge deployment boundary

VoxBridge 0.4.0 is a private developer alpha. This document separates the topology supported by the repository from work required for a public service.

## Implemented in this repository

- stdio and Streamable HTTP MCP transports.
- Loopback HTTP by default (`127.0.0.1:8000/mcp`).
- A fail-closed check for non-loopback HTTP. `VOXBRIDGE_ALLOW_REMOTE_BIND=true` is required to acknowledge a deliberate remote bind.
- Provider credentials loaded server-side from the process environment or local `.env`.
- Per-call `playback`, `file`, or `both` delivery, with opaque short-lived MCP file resources held in a bounded process-local cache and a standard MCP App Download control for file-capable results.
- Ordered, single-provider multi-voice dialogue assembled into one bounded PCM WAV file, with one upstream synthesis call per segment.
- ElevenLabs Music generation from exactly one prompt or bounded composition-plan JSON object, with `music_v2_5` as the default model, bounded prompt-mode duration, MP3 output, and the same `playback`, `file`, or `both` delivery choices.
- Optional embedded-byte materialization for compatible downstream tools and a user-initiated **Add file to ChatGPT** App action; the host may request approval, and compatible hosts can register the returned resource as an attachment, but reusable-library persistence and universal downstream audio support are not implied.
- `/healthz` and provider-aware `/readyz` endpoints.
- Container packaging.
- A repository-root private-hosting image with the official OpenAI `tunnel-client` pinned by version and immutable multi-platform digest.
- A supervisor that keeps VoxBridge on loopback and gives `tunnel-client` a stateless Streamable HTTP target.
- GitHub Actions publishing to `ghcr.io/gds-g/voxbridge-gateway`.

The remote-bind override is not authentication, authorization, or encryption.

## Private developer topologies

Prefer these in order:

1. **stdio:** let a local MCP client launch `voxbridge` with `VOXBRIDGE_TRANSPORT=stdio`. This creates no network listener.
2. **Loopback HTTP:** use the default `http://127.0.0.1:8000/mcp` from a client on the same host.
3. **Secure MCP Tunnel:** keep VoxBridge on loopback and terminate a separately managed private tunnel on that loopback endpoint. The repository-root hosted image implements this process topology.

OpenAI Platform still provisions the tunnel ID and runtime API key. They are runtime secrets and are never built into the image. A tunnel connection is not evidence that the gateway is public-ready.

## Recommended hosted private alpha

Use GitHub for source, CI, and the GHCR image, and use Railway as the always-on compute host. GitHub Pages is static hosting and cannot run this Python service. Railway currently requires either its limited new-user trial or a paid Hobby plan; review the current price before activating it.

1. Push this repository and let both GitHub workflows pass. The container workflow repeats the quality gate, smoke-tests an amd64 image, and only then publishes `ghcr.io/gds-g/voxbridge-gateway:latest` plus a commit-derived `sha-*` tag. Registry tags can move; record and pin the published OCI digest for an immutable deployment.
2. In OpenAI Platform, create or select a Secure MCP Tunnel and create a separate runtime API key whose principal has **Tunnels Read + Use**. Do not use an admin key for the daemon.
3. In Railway, create one private persistent service from `GDS-G/VoxxBridge`. The root `Dockerfile` is detected automatically. Do not generate a public domain and disable Serverless/sleep behavior.
4. Add sealed Railway variables for `CONTROL_PLANE_TUNNEL_ID`, `CONTROL_PLANE_API_KEY`, and at least one provider's required variables. Do not paste their values into chat, source control, build arguments, or image labels.
5. Keep one normal replica. Generated file resources are process-local, so a resource created on one replica cannot be read from another and a restart invalidates outstanding links. A short deployment overlap is safe for stateless discovery and generation only when old and new replicas are protocol-compatible; do not rely on file downloads across that overlap. Provider concurrency and quotas apply independently to every replica.
6. Deploy, confirm the logs show a successful tunnel connection, and verify tunnel readiness—not only process liveness—before connecting a client. Then enable ChatGPT developer mode, create the connector using **Tunnel**, and select the same tunnel ID.
7. Run `list_providers`, verify the intended provider reports `configured: true`, list a few voices, and make a short, low-cost synthesis before treating the deployment as usable. Exercise `generate_speech` with `delivery="playback"`, `delivery="file"`, and `delivery="both"`; for file delivery, read the returned resource and confirm it opens as the requested format. Make one short `generate_dialogue` request with two voice IDs and verify turn order and the combined WAV. When ElevenLabs Music is intentionally in scope, make exactly one low-cost 3-second `generate_music` prompt request with `output_format="mp3"` and `delivery="file"`; confirm the resource contains non-empty `audio/mpeg` bytes, and reuse that result for any attachment or download checks rather than generating again. Music can consume paid quota and requires the account owner's authorization and current API entitlement. In ChatGPT, click the MCP App Download button and confirm the host saves a playable file when it advertises `ui/download-file`. Also choose **Add file to ChatGPT**, approve the app-visible `materialize_audio_file` call if prompted, and verify the returned attachment has the original byte count and SHA-256 without another generation call. When invoking materialization outside the App, use the exact `materialize_resource_uri`; if the host hides that custom URI, use the exact generated `file_name` fallback. Treat successful materialization as proof that ChatGPT received the exact bytes, but do not infer reusable-library persistence or that every downstream editor accepts audio. For an approval-sensitive live check, confirm an identical host replay returns the same generated filename and does not add provider calls within the two-minute replay window.

The container deliberately exposes no public application port. Its local tunnel health UI listens on `127.0.0.1:8080`, and VoxBridge listens on `127.0.0.1:8000`; both remain inside the container. The image-level `/healthz` probe checks tunnel-client liveness. Tunnel `/readyz`, the OpenAI tunnel status, and a successful MCP discovery establish readiness. Do not configure Railway's external HTTP healthcheck against this loopback listener. OpenAI control-plane traffic is outbound HTTPS to `api.openai.com:443`.

The required deployment variables are:

| Variable | Purpose |
| --- | --- |
| `CONTROL_PLANE_TUNNEL_ID` | OpenAI Secure MCP Tunnel identifier. |
| `CONTROL_PLANE_API_KEY` | Dedicated runtime key for the tunnel daemon. |
| Provider variables | At least one provider credential set from the table in `README.md`. |

Optional operational defaults such as `VOXBRIDGE_MAX_TEXT_CHARS`, `VOXBRIDGE_MAX_AUDIO_BYTES`, `VOXBRIDGE_MAX_CONCURRENT_GENERATIONS`, the `VOXBRIDGE_MAX_DIALOGUE_*` limits, `VOXBRIDGE_MAX_MATERIALIZED_AUDIO_BYTES`, `VOXBRIDGE_AUDIO_DOWNLOAD_TTL_SECONDS`, `VOXBRIDGE_AUDIO_DOWNLOAD_MAX_ITEMS`, `VOXBRIDGE_AUDIO_DOWNLOAD_MAX_BYTES`, `VOXBRIDGE_REQUEST_TIMEOUT_SECONDS`, and `VOXBRIDGE_MUSIC_REQUEST_TIMEOUT_SECONDS` can also be set as sealed variables. The hosted launcher always forces the gateway transport to loopback Streamable HTTP and ignores attempts to replace its internal MCP target.

If a sidecar or isolated container network requires VoxBridge to listen on a non-loopback interface, launch standalone `voxbridge` or provide a custom entrypoint after establishing the private network boundary, then set both `VOXBRIDGE_HOST` and `VOXBRIDGE_ALLOW_REMOTE_BIND=true`. The supplied `voxbridge-hosted` launcher always forces loopback. Host and Origin validation remains active; configure the exact private proxy host/origin through `VOXBRIDGE_ALLOWED_HOSTS` and `VOXBRIDGE_ALLOWED_ORIGINS` if the loopback defaults do not match. Never use broad wildcard domains or treat the override as a security control.

### Docker networking

A process listening on `127.0.0.1` inside a container generally cannot receive traffic forwarded through a Docker published port. For local-only host access, use `VOXBRIDGE_HOST=0.0.0.0` inside the container and publish with `-p 127.0.0.1:8000:8000`. Publishing as `-p 8000:8000` can expose the service beyond host loopback and is outside the private developer-alpha posture.

## Not deployed or verified by this repository

- No stable hosted HTTPS endpoint is declared.
- No OAuth flow or per-user authorization layer is implemented.
- No encrypted per-user provider credential vault is implemented.
- No OpenAI tunnel, runtime key, or provider credential is provisioned by this repository.
- CI does not call provider APIs and therefore does not establish live provider compatibility, credentials, entitlements, quotas, policy compliance, or audio quality.
- The repository does not establish provider review, endorsement, or approval.

## Public multi-user release requirements

Before public distribution:

1. Put the MCP service behind a stable HTTPS origin.
2. Authenticate MCP clients to VoxBridge with OAuth and enforce authorization server-side.
3. Map each OAuth subject to a VoxBridge user account.
4. Let users connect provider credentials through a secure VoxBridge surface; never request API keys in chat.
5. Encrypt credentials at rest with managed key protection, decrypt them only for outbound provider calls, and make connections independently revocable.
6. Add per-user usage metering, rate limits, audit events, abuse controls, and retention/deletion controls.
7. Review each provider’s current terms, acceptable-use rules, cloning/impersonation restrictions, consent requirements, and synthetic-media disclosure requirements.
8. Verify every enabled integration against a real authorized account and record what was tested.
9. Reassess how large or long audio is delivered; short-lived signed object-storage URLs may be more appropriate than inline audio.

Only after the stable HTTPS service and OAuth controls are live should the plugin point to the public endpoint:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "voxbridge": {
      "type": "streamable-http",
      "url": "https://YOUR_STABLE_HOST/mcp"
    }
  }
}
```

`YOUR_STABLE_HOST` is documentation only. Do not publish a plugin containing a placeholder or an unverified tunnel URL.
