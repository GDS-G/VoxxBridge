from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any, Literal
from uuid import uuid4

import uvicorn
from mcp.server import MCPServer
from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import AudioContent, ResourceLink, TextContent, ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from voxbridge import __version__
from voxbridge.audio_store import AudioArtifactStore
from voxbridge.config import settings
from voxbridge.models import SpeechRequest
from voxbridge.providers.base import ProviderError
from voxbridge.registry import REGISTRY, close_registry

_AUDIO_DELIVERY_UI_URI = "ui://voxbridge/audio-delivery-v1.html"
_AUDIO_DELIVERY_UI_HTML = (
    files("voxbridge").joinpath("ui").joinpath("audio-delivery-v1.html").read_text(encoding="utf-8")
)
_apps = Apps()
_apps.add_html_resource(
    _AUDIO_DELIVERY_UI_URI,
    _AUDIO_DELIVERY_UI_HTML,
    name="voxbridge-audio-delivery",
    title="VoxBridge audio delivery",
    description="Play generated VoxBridge audio, download it as a file, or do both.",
    csp=ResourceCsp(connect_domains=[], resource_domains=[]),
    prefers_border=True,
)


@asynccontextmanager
async def _lifespan(_: MCPServer):
    try:
        yield None
    finally:
        _audio_artifacts.clear()
        await close_registry()


mcp = MCPServer(
    "VoxBridge",
    version=__version__,
    instructions=(
        "Provider-neutral text-to-speech. Never switch providers silently. "
        "Generated audio is synthetic. Use delivery='playback', 'file', or 'both' "
        "to choose the returned representation."
    ),
    extensions=[_apps],
    lifespan=_lifespan,
    log_level=settings.voxbridge_log_level,
)

_generation_slots = asyncio.Semaphore(settings.voxbridge_max_concurrent_generations)
_audio_artifacts = AudioArtifactStore(
    ttl_seconds=settings.voxbridge_audio_download_ttl_seconds,
    max_items=settings.voxbridge_audio_download_max_items,
    max_bytes=settings.voxbridge_audio_download_max_bytes,
)

_AUDIO_FILE_TYPES = {
    "audio/aac": ("aac", "aac", "audio/aac"),
    "audio/flac": ("flac", "flac", "audio/flac"),
    "audio/mp3": ("mp3", "mp3", "audio/mpeg"),
    "audio/mp4": ("m4a", "m4a", "audio/mp4"),
    "audio/mpeg": ("mp3", "mp3", "audio/mpeg"),
    "audio/ogg": ("ogg", "ogg", "audio/ogg"),
    "audio/opus": ("opus", "opus", "audio/opus"),
    "audio/vnd.wave": ("wav", "wav", "audio/wav"),
    "audio/wav": ("wav", "wav", "audio/wav"),
    "audio/webm": ("webm", "webm", "audio/webm"),
    "audio/x-flac": ("flac", "flac", "audio/flac"),
    "audio/x-wav": ("wav", "wav", "audio/wav"),
}


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")


def _audio_file_details(mime_type: str) -> tuple[str, str, str]:
    media_type = mime_type.partition(";")[0].strip().lower()
    details = _AUDIO_FILE_TYPES.get(media_type)
    if details is None:
        raise ToolError(f"Provider returned an unsupported audio media type '{media_type}'")
    return details


def _download_filename(extension: str) -> str:
    return f"voxbridge-{uuid4().hex}.{extension}"


def _resource_uri(format_id: str, token: str, file_name: str) -> str:
    return f"voxbridge://audio/{format_id}/{token}/{file_name}"


def _read_audio_artifact(
    token: str,
    file_name: str,
    *,
    expected_format: str,
) -> bytes:
    artifact = _audio_artifacts.get(token)
    if artifact is None or artifact.file_name != file_name or artifact.format_id != expected_format:
        raise ResourceError("Audio file is unavailable or has expired")
    return artifact.data


@mcp.resource(
    "voxbridge://audio/mp3/{token}/{file_name}",
    name="generated-mp3",
    title="Generated MP3 audio",
    mime_type="audio/mpeg",
)
def generated_mp3(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="mp3")


@mcp.resource(
    "voxbridge://audio/wav/{token}/{file_name}",
    name="generated-wav",
    title="Generated WAV audio",
    mime_type="audio/wav",
)
def generated_wav(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="wav")


@mcp.resource(
    "voxbridge://audio/ogg/{token}/{file_name}",
    name="generated-ogg",
    title="Generated OGG audio",
    mime_type="audio/ogg",
)
def generated_ogg(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="ogg")


@mcp.resource(
    "voxbridge://audio/opus/{token}/{file_name}",
    name="generated-opus",
    title="Generated Opus audio",
    mime_type="audio/opus",
)
def generated_opus(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="opus")


@mcp.resource(
    "voxbridge://audio/aac/{token}/{file_name}",
    name="generated-aac",
    title="Generated AAC audio",
    mime_type="audio/aac",
)
def generated_aac(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="aac")


@mcp.resource(
    "voxbridge://audio/flac/{token}/{file_name}",
    name="generated-flac",
    title="Generated FLAC audio",
    mime_type="audio/flac",
)
def generated_flac(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="flac")


@mcp.resource(
    "voxbridge://audio/m4a/{token}/{file_name}",
    name="generated-m4a",
    title="Generated M4A audio",
    mime_type="audio/mp4",
)
def generated_m4a(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="m4a")


@mcp.resource(
    "voxbridge://audio/webm/{token}/{file_name}",
    name="generated-webm",
    title="Generated WebM audio",
    mime_type="audio/webm",
)
def generated_webm(token: str, file_name: str) -> bytes:
    return _read_audio_artifact(token, file_name, expected_format="webm")


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
    meta={
        "ui": {"resourceUri": _AUDIO_DELIVERY_UI_URI},
        "openai/outputTemplate": _AUDIO_DELIVERY_UI_URI,
    },
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
    delivery: Literal["playback", "file", "both"] = "both",
) -> list[TextContent | AudioContent | ResourceLink]:
    """Generate speech for playback, file download, or both from one provider call.

    `instructions` is provider-aware: Hume Octave 1 treats it as acting direction and
    OpenAI treats it as voice instructions on supported models. `options_json` is for
    documented provider-specific controls such as ElevenLabs stability/similarity,
    Cartesia emotion/volume, or Resemble HD synthesis. `delivery` controls whether the
    result contains playable audio, a short-lived named file resource, or both. Raw PCM is
    playback-only; request WAV when `delivery` is `file` or `both`.
    """
    if not text.strip():
        raise ToolError("text cannot be empty")
    if delivery not in {"playback", "file", "both"}:
        raise ToolError("delivery must be 'playback', 'file', or 'both'")
    normalized_output_format = output_format.strip().lower()
    if delivery in {"file", "both"} and normalized_output_format == "pcm":
        raise ToolError(
            "PCM file delivery is unavailable in this developer alpha because raw PCM "
            "does not carry portable sample metadata; use WAV instead"
        )
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
        output_format=normalized_output_format,
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
    include_playback = delivery in {"playback", "both"}
    include_file = delivery in {"file", "both"}
    metadata = {
        **result.metadata,
        "synthetic_audio": True,
        "provider": result.provider,
        "model": result.model,
        "mime_type": result.mime_type,
        "request_id": result.request_id,
        "delivery": delivery,
        "playback_included": include_playback,
        "file_resource_included": include_file,
        "file_size_bytes": len(result.audio),
    }
    content: list[TextContent | AudioContent | ResourceLink] = []
    audio_content: AudioContent | None = None
    if include_playback:
        audio_content = AudioContent(
            type="audio",
            data=base64.b64encode(result.audio).decode("ascii"),
            mimeType=result.mime_type,
        )

    file_resource: ResourceLink | None = None
    if include_file:
        format_id, extension, file_mime_type = _audio_file_details(result.mime_type)
        file_name = _download_filename(extension)
        try:
            token, artifact = _audio_artifacts.put(
                result.audio,
                mime_type=file_mime_type,
                format_id=format_id,
                file_name=file_name,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        resource_uri = _resource_uri(format_id, token, file_name)
        expires_at = (
            datetime.fromtimestamp(artifact.expires_at, tz=UTC).isoformat().replace("+00:00", "Z")
        )
        metadata.update(
            {
                "file_name": file_name,
                "file_mime_type": file_mime_type,
                "resource_uri": resource_uri,
                "download_expires_at": expires_at,
            }
        )
        file_resource = ResourceLink(
            type="resource_link",
            uri=resource_uri,
            name=file_name,
            title=f"Download {file_name}",
            description="Short-lived downloadable synthetic audio generated by VoxBridge.",
            mimeType=file_mime_type,
            size=len(result.audio),
        )

    content.append(TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)))
    if audio_content is not None:
        content.append(audio_content)
    if file_resource is not None:
        content.append(file_resource)
    return content


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
