from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import AudioContent, TextContent, ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from voxbridge import __version__
from voxbridge.config import settings
from voxbridge.models import SpeechRequest
from voxbridge.providers.base import ProviderError
from voxbridge.registry import REGISTRY, close_registry


@asynccontextmanager
async def _lifespan(_: MCPServer):
    try:
        yield None
    finally:
        await close_registry()


mcp = MCPServer(
    "VoxBridge",
    version=__version__,
    instructions=(
        "Provider-neutral text-to-speech. Never switch providers silently. "
        "Generated audio is synthetic."
    ),
    lifespan=_lifespan,
    log_level=settings.voxbridge_log_level,
)

_generation_slots = asyncio.Semaphore(settings.voxbridge_max_concurrent_generations)


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")


def _provider(provider: str):
    key = provider.strip().lower()
    if key not in REGISTRY:
        raise ToolError(f"Unknown provider '{provider}'. Use list_providers first.")
    p = REGISTRY[key]
    if not p.configured:
        raise ToolError(f"{p.display_name} is not connected on this VoxBridge gateway")
    return p


@mcp.tool(
    title="List voice providers",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
def list_providers() -> list[dict[str, Any]]:
    """List every VoxBridge provider, connection state, default model, and supported capabilities."""
    return [p.info() for p in REGISTRY.values()]


@mcp.tool(
    title="List provider voices",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_voices(
    provider: str, language: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """List voices from one provider. Use provider IDs from list_providers."""
    p = _provider(provider)
    try:
        voices = await p.list_voices(language=language, limit=max(1, min(limit, 100)))
    except ProviderError as exc:
        raise ToolError(str(exc)) from exc
    return [
        {
            "id": v.id,
            "name": v.name,
            "provider": v.provider,
            "language": v.language,
            "gender": v.gender,
            "description": v.description,
            "preview_url": v.preview_url,
            "metadata": v.metadata,
        }
        for v in voices
    ]


@mcp.tool(
    title="Generate realistic speech",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def generate_speech(
    provider: str,
    text: str,
    voice_id: str | None = None,
    model: str | None = None,
    output_format: str = "mp3",
    instructions: str | None = None,
    speed: float | None = None,
    language: str | None = None,
    options_json: str | None = None,
) -> list[TextContent | AudioContent]:
    """Generate speech through any configured provider and return playable audio.

    `instructions` is provider-aware: Hume Octave 1 treats it as acting direction and
    OpenAI treats it as voice instructions on supported models. `options_json` is for
    documented provider-specific controls such as ElevenLabs stability/similarity,
    Cartesia emotion/volume, or Resemble HD synthesis.
    """
    if not text.strip():
        raise ToolError("text cannot be empty")
    if len(text) > settings.voxbridge_max_text_chars:
        raise ToolError(
            f"text exceeds the {settings.voxbridge_max_text_chars:,}-character "
            "developer-alpha limit"
        )
    options: dict[str, Any] = {}
    if options_json:
        try:
            parsed = json.loads(
                options_json,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ToolError("options_json must contain valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ToolError("options_json must decode to a JSON object")
        options = parsed
    p = _provider(provider)
    request = SpeechRequest(
        text=text,
        voice_id=voice_id,
        model=model,
        output_format=output_format.strip().lower(),
        instructions=instructions,
        speed=speed,
        language=language,
        options=options,
    )
    try:
        p.validate_request(request)
        async with _generation_slots:
            result = await p.generate(request)
        p.validate_audio(result.audio)
    except ProviderError as exc:
        raise ToolError(str(exc)) from exc
    if len(result.audio) > settings.voxbridge_max_audio_bytes:
        raise ToolError(
            f"Generated audio exceeds the {settings.voxbridge_max_audio_bytes:,}-byte limit"
        )
    if not result.mime_type.startswith("audio/"):
        raise ToolError("Provider returned an invalid audio media type")
    metadata = {
        **result.metadata,
        "synthetic_audio": True,
        "provider": result.provider,
        "model": result.model,
        "mime_type": result.mime_type,
        "request_id": result.request_id,
    }
    return [
        TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
        AudioContent(
            type="audio",
            data=base64.b64encode(result.audio).decode("ascii"),
            mimeType=result.mime_type,
        ),
    ]


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "voxbridge", "version": __version__})


async def _ready(_: Request) -> JSONResponse:
    configured = sorted(provider.id for provider in REGISTRY.values() if provider.configured)
    status = 200 if configured else 503
    return JSONResponse(
        {"status": "ready" if configured else "not_ready", "providers": configured},
        status_code=status,
    )


def build_http_app():
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.voxbridge_allowed_hosts,
        allowed_origins=settings.voxbridge_allowed_origins,
    )
    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        max_request_body_size=64 * 1024,
        transport_security=transport_security,
        host=settings.voxbridge_host,
    )
    app.router.routes.insert(0, Route("/healthz", _health, methods=["GET"]))
    app.router.routes.insert(1, Route("/readyz", _ready, methods=["GET"]))
    return app


def main() -> None:
    if settings.voxbridge_transport == "stdio":
        mcp.run(transport="stdio")
        return
    settings.assert_safe_bind()
    app = build_http_app()
    uvicorn.run(
        app,
        host=settings.voxbridge_host,
        port=settings.voxbridge_port,
        log_level=settings.voxbridge_log_level.lower(),
    )


if __name__ == "__main__":
    main()
