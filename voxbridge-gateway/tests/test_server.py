from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import AudioContent, ResourceLink, TextContent
from mcp_types import LATEST_PROTOCOL_VERSION
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

from voxbridge import __version__, server
from voxbridge.config import Settings
from voxbridge.models import SpeechResult, Voice
from voxbridge.providers.base import ProviderError


class FakeProvider:
    id = "fake"
    display_name = "Fake Voice"
    configured = True

    def __init__(self, result: SpeechResult | None = None) -> None:
        self.result = result or SpeechResult(
            audio=b"fake-audio",
            mime_type="audio/wav",
            provider=self.id,
            model="fake-model",
            request_id="fake-request",
            metadata={"duration": 1.25},
        )
        self.validated = []
        self.generated = []
        self.closed = False

    def info(self):
        return {
            "id": self.id,
            "name": self.display_name,
            "configured": self.configured,
            "default_model": "fake-model",
            "capabilities": ["text_to_speech", "list_voices"],
            "supported_formats": ["wav"],
            "max_text_chars": 3_000,
            "supports_instructions": True,
            "supports_language": True,
            "supports_speed": True,
        }

    def validate_request(self, request) -> None:
        self.validated.append(request)

    async def generate(self, request) -> SpeechResult:
        self.generated.append(request)
        return self.result

    def validate_audio(self, audio: bytes) -> None:
        if not audio:
            raise ProviderError("Fake Voice returned empty audio")

    async def list_voices(self, *, language=None, limit=50):
        return [Voice("voice-1", "Voice One", self.id, language=language)][:limit]

    async def aclose(self) -> None:
        self.closed = True


class FailingProvider(FakeProvider):
    def __init__(self, message="safe provider failure") -> None:
        super().__init__()
        self.message = message

    async def list_voices(self, *, language=None, limit=50):
        raise ProviderError(self.message)

    async def generate(self, request) -> SpeechResult:
        raise ProviderError(self.message)


@pytest.fixture(autouse=True)
def clear_audio_artifacts():
    server._audio_artifacts.clear()
    yield
    server._audio_artifacts.clear()


async def test_generate_speech_returns_metadata_playable_audio_and_downloadable_file(
    monkeypatch,
):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.generate_speech(
        provider=" FAKE ",
        text="Hello world",
        voice_id="voice-1",
        model="fake-model",
        output_format=" WAV ",
        instructions="Warm and clear",
        speed=1.1,
        language="en-US",
        options_json='{"tone": "bright"}',
    )

    assert len(provider.validated) == 1
    assert provider.generated == provider.validated
    request = provider.generated[0]
    assert request.text == "Hello world"
    assert request.voice_id == "voice-1"
    assert request.model == "fake-model"
    assert request.output_format == "wav"
    assert request.instructions == "Warm and clear"
    assert request.speed == 1.1
    assert request.language == "en-US"
    assert request.options == {"tone": "bright"}

    assert len(output) == 3
    assert isinstance(output[0], TextContent)
    metadata = json.loads(output[0].text)
    assert metadata == {
        "synthetic_audio": True,
        "provider": "fake",
        "model": "fake-model",
        "mime_type": "audio/wav",
        "request_id": "fake-request",
        "duration": 1.25,
        "delivery": "both",
        "playback_included": True,
        "file_resource_included": True,
        "file_name": metadata["file_name"],
        "file_mime_type": "audio/wav",
        "file_size_bytes": len(b"fake-audio"),
        "resource_uri": metadata["resource_uri"],
        "download_expires_at": metadata["download_expires_at"],
    }
    assert re.fullmatch(r"voxbridge-[0-9a-f]{32}\.wav", metadata["file_name"])
    assert metadata["resource_uri"].endswith(f"/{metadata['file_name']}")
    assert metadata["download_expires_at"].endswith("Z")
    assert isinstance(output[1], AudioContent)
    assert output[1].mime_type == "audio/wav"
    assert base64.b64decode(output[1].data) == b"fake-audio"
    assert isinstance(output[2], ResourceLink)
    assert output[2].uri == metadata["resource_uri"]
    assert output[2].name == metadata["file_name"]
    assert output[2].mime_type == "audio/wav"
    assert output[2].size == len(b"fake-audio")


@pytest.mark.parametrize(
    ("mime_type", "extension", "canonical_mime_type"),
    [
        ("audio/mpeg", "mp3", "audio/mpeg"),
        ("audio/mp3", "mp3", "audio/mpeg"),
        ("audio/wav", "wav", "audio/wav"),
        ("audio/x-wav", "wav", "audio/wav"),
        ("audio/opus", "opus", "audio/opus"),
        ("audio/ogg", "ogg", "audio/ogg"),
        ("audio/aac", "aac", "audio/aac"),
        ("audio/flac", "flac", "audio/flac"),
        ("audio/x-flac", "flac", "audio/flac"),
    ],
)
def test_download_filename_uses_safe_extension(mime_type, extension, canonical_mime_type):
    _, actual_extension, actual_mime_type = server._audio_file_details(mime_type)
    file_name = server._download_filename(actual_extension)

    assert actual_mime_type == canonical_mime_type
    assert re.fullmatch(rf"voxbridge-[0-9a-f]{{32}}\.{extension}", file_name)
    assert "/" not in file_name
    assert "\\" not in file_name


def test_file_delivery_rejects_unknown_audio_media_type():
    with pytest.raises(ToolError, match="unsupported audio media type"):
        server._audio_file_details("audio/x-vendor-format")


@pytest.mark.parametrize(
    ("provider_mime_type", "file_mime_type"),
    [
        ("audio/mpeg", "audio/mpeg"),
        ("audio/mp3", "audio/mpeg"),
        ("audio/wav", "audio/wav"),
        ("audio/x-wav", "audio/wav"),
        ("audio/ogg", "audio/ogg"),
        ("audio/opus", "audio/opus"),
        ("audio/aac", "audio/aac"),
        ("audio/flac", "audio/flac"),
        ("audio/x-flac", "audio/flac"),
        ("audio/mp4", "audio/mp4"),
        ("audio/webm", "audio/webm"),
    ],
)
async def test_file_resource_preserves_canonical_mime_type(
    monkeypatch,
    provider_mime_type,
    file_mime_type,
):
    provider = FakeProvider(
        SpeechResult(
            audio=b"format-audio",
            mime_type=provider_mime_type,
            provider="fake",
        )
    )
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.generate_speech("fake", "Hello", delivery="file")
    metadata = json.loads(output[0].text)
    link = output[1]
    read_result = list(await server.mcp.read_resource(link.uri))

    assert metadata["file_mime_type"] == file_mime_type
    assert link.mime_type == file_mime_type
    assert len(read_result) == 1
    assert read_result[0].mime_type == file_mime_type
    assert read_result[0].content == b"format-audio"


async def test_download_metadata_cannot_be_overridden_by_provider(monkeypatch):
    provider = FakeProvider(
        SpeechResult(
            audio=b"safe-audio",
            mime_type="audio/mpeg",
            provider="fake",
            metadata={
                "file_name": "../../provider-name.exe",
                "file_size_bytes": -1,
                "file_resource_included": False,
            },
        )
    )
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.generate_speech("fake", "private script text")
    metadata = json.loads(output[0].text)

    assert re.fullmatch(r"voxbridge-[0-9a-f]{32}\.mp3", metadata["file_name"])
    assert metadata["file_size_bytes"] == len(b"safe-audio")
    assert metadata["file_resource_included"] is True
    assert "private" not in metadata["file_name"]
    assert output[2].uri == metadata["resource_uri"]


@pytest.mark.parametrize(
    ("delivery", "content_types", "playback_included", "file_included"),
    [
        ("playback", ["text", "audio"], True, False),
        ("file", ["text", "resource_link"], False, True),
        ("both", ["text", "audio", "resource_link"], True, True),
    ],
)
async def test_delivery_selects_playback_file_or_both(
    monkeypatch,
    delivery,
    content_types,
    playback_included,
    file_included,
):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.generate_speech("fake", "Hello", delivery=delivery)
    metadata = json.loads(output[0].text)

    assert [item.type for item in output] == content_types
    assert metadata["delivery"] == delivery
    assert metadata["playback_included"] is playback_included
    assert metadata["file_resource_included"] is file_included
    assert len(provider.generated) == 1
    assert server._audio_artifacts.item_count == int(file_included)
    if file_included:
        assert metadata["file_name"]
        assert metadata["resource_uri"]
        assert metadata["download_expires_at"]
    else:
        assert "file_name" not in metadata
        assert "resource_uri" not in metadata
        assert "download_expires_at" not in metadata


async def test_invalid_delivery_is_rejected_before_generation(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match="delivery must be"):
        await server.generate_speech("fake", "Hello", delivery="stream")

    assert provider.generated == []


@pytest.mark.parametrize("delivery", ["file", "both"])
async def test_pcm_file_delivery_is_rejected_before_generation(monkeypatch, delivery):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match="PCM file delivery.*use WAV"):
        await server.generate_speech("fake", "Hello", output_format="pcm", delivery=delivery)

    assert provider.generated == []


def test_audio_resource_capability_checks_are_non_disclosing():
    token, artifact = server._audio_artifacts.put(
        b"private-audio",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="voxbridge-test.mp3",
    )

    assert (
        server._read_audio_artifact(
            token,
            artifact.file_name,
            expected_format=artifact.format_id,
        )
        == b"private-audio"
    )
    for bad_token, bad_name, bad_format in (
        ("unknown", artifact.file_name, artifact.format_id),
        (token, "other.mp3", artifact.format_id),
        (token, artifact.file_name, "wav"),
    ):
        with pytest.raises(ResourceError, match="unavailable or has expired"):
            server._read_audio_artifact(
                bad_token,
                bad_name,
                expected_format=bad_format,
            )


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_mcp_client_receives_playback_and_downloadable_file(monkeypatch, mode):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    async with Client(server.mcp, mode=mode) as client:
        result = await client.call_tool(
            "generate_speech",
            {"provider": "fake", "text": "Hello"},
        )

        assert result.is_error is False
        assert [item.type for item in result.content] == ["text", "audio", "resource_link"]
        metadata = json.loads(result.content[0].text)
        audio = result.content[1]
        downloadable = result.content[2]
        resource = await client.read_resource(downloadable.uri, cache_mode="bypass")

    assert downloadable.uri == metadata["resource_uri"]
    assert downloadable.name == metadata["file_name"]
    assert downloadable.mime_type == audio.mime_type == "audio/wav"
    assert len(resource.contents) == 1
    assert resource.contents[0].mime_type == "audio/wav"
    assert base64.b64decode(resource.contents[0].blob) == b"fake-audio"


@pytest.mark.parametrize(
    "mode",
    ["auto", LATEST_PROTOCOL_VERSION, "legacy"],
    ids=["auto-current", "explicit-current", "legacy"],
)
async def test_mcp_in_process_discovery_in_current_and_legacy_modes(mode, monkeypatch):
    async def do_not_close_global_registry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "close_registry", do_not_close_global_registry)

    async with Client(server.mcp, mode=mode) as client:
        result = await client.list_tools(cache_mode="reload")
        protocol_version = client.session.protocol_version

    assert {tool.name for tool in result.tools} == {
        "list_providers",
        "list_voices",
        "generate_speech",
    }
    by_name = {tool.name: tool for tool in result.tools}
    assert by_name["list_providers"].annotations.read_only_hint is True
    assert by_name["list_voices"].annotations.read_only_hint is True
    assert by_name["generate_speech"].annotations.read_only_hint is False
    assert set(by_name["generate_speech"].input_schema["required"]) == {"provider", "text"}
    delivery_schema = by_name["generate_speech"].input_schema["properties"]["delivery"]
    assert delivery_schema["default"] == "both"
    assert set(delivery_schema["enum"]) == {"playback", "file", "both"}
    generate_meta = by_name["generate_speech"].meta
    assert generate_meta["ui"]["resourceUri"] == server._AUDIO_DELIVERY_UI_URI
    assert generate_meta["openai/outputTemplate"] == server._AUDIO_DELIVERY_UI_URI
    if mode == "legacy":
        assert protocol_version in HANDSHAKE_PROTOCOL_VERSIONS
    else:
        assert protocol_version == LATEST_PROTOCOL_VERSION


async def test_mcp_audio_delivery_app_resource_is_discoverable(monkeypatch):
    async def do_not_close_global_registry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "close_registry", do_not_close_global_registry)

    async with Client(server.mcp, mode="auto") as client:
        extensions = client.session.server_capabilities.extensions
        resources = await client.list_resources(cache_mode="reload")
        ui_resource = next(
            item for item in resources.resources if str(item.uri) == server._AUDIO_DELIVERY_UI_URI
        )
        result = await client.read_resource(server._AUDIO_DELIVERY_UI_URI, cache_mode="bypass")

    assert extensions is not None
    assert "io.modelcontextprotocol/ui" in extensions
    assert ui_resource.mime_type == "text/html;profile=mcp-app"
    assert ui_resource.meta == {
        "ui": {
            "csp": {"connectDomains": [], "resourceDomains": []},
            "prefersBorder": True,
        }
    }
    assert len(result.contents) == 1
    document = result.contents[0]
    assert document.mime_type == "text/html;profile=mcp-app"
    assert "VoxBridge" in document.text
    assert "downloadFile" in document.text
    assert 'id="play-pause"' in document.text
    assert "new Audio(" in document.text
    assert "media-src data:" in document.text


async def test_mcp_tool_error_is_returned_as_safe_tool_content(monkeypatch):
    async def do_not_close_global_registry(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "close_registry", do_not_close_global_registry)
    monkeypatch.setattr(server, "REGISTRY", {})

    async with Client(server.mcp, mode="auto") as client:
        result = await client.call_tool(
            "generate_speech",
            {"provider": "does-not-exist", "text": "Hello"},
        )

    assert result.is_error is True
    assert len(result.content) == 1
    assert "Unknown provider 'does-not-exist'" in result.content[0].text


def test_unknown_and_unconfigured_provider_errors(monkeypatch):
    monkeypatch.setattr(server, "REGISTRY", {})
    with pytest.raises(ToolError, match="Unknown provider"):
        server._provider(" missing ")

    disconnected = FakeProvider()
    disconnected.configured = False
    monkeypatch.setattr(server, "REGISTRY", {disconnected.id: disconnected})
    with pytest.raises(ToolError, match="Fake Voice is not connected"):
        server._provider("FAKE")


@pytest.mark.parametrize(
    ("options_json", "message"),
    [
        ("{", "options_json must contain valid JSON"),
        ('{"value": NaN}', "options_json must contain valid JSON"),
        ('{"value": Infinity}', "options_json must contain valid JSON"),
        ("[]", "options_json must decode to a JSON object"),
        ("null", "options_json must decode to a JSON object"),
    ],
)
async def test_generate_speech_rejects_invalid_options_json(monkeypatch, options_json, message):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match=message):
        await server.generate_speech("fake", "Hello", options_json=options_json)

    assert provider.validated == []
    assert provider.generated == []


async def test_generate_speech_rejects_empty_and_oversized_text(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})
    monkeypatch.setattr(server.settings, "voxbridge_max_text_chars", 5)

    with pytest.raises(ToolError, match="text cannot be empty"):
        await server.generate_speech("fake", " \n\t")
    with pytest.raises(ToolError, match="text exceeds the 5-character"):
        await server.generate_speech("fake", "123456")

    assert provider.generated == []


async def test_provider_failures_become_tool_errors(monkeypatch):
    provider = FailingProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match="safe provider failure"):
        await server.list_voices("fake")
    with pytest.raises(ToolError, match="safe provider failure"):
        await server.generate_speech("fake", "Hello")


async def test_list_voices_clamps_limit_and_serializes_voice(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.list_voices("fake", language="en-US", limit=0)

    assert output == [
        {
            "id": "voice-1",
            "name": "Voice One",
            "provider": "fake",
            "language": "en-US",
            "gender": None,
            "description": None,
            "preview_url": None,
            "metadata": {},
        }
    ]


@pytest.mark.parametrize(
    ("result", "audio_limit", "message"),
    [
        (
            SpeechResult(b"audio", "application/octet-stream", "fake"),
            100,
            "Provider returned an invalid audio media type",
        ),
        (
            SpeechResult(b"four", "audio/wav", "fake"),
            3,
            "Generated audio exceeds the 3-byte limit",
        ),
    ],
)
async def test_generate_speech_rejects_invalid_provider_results(
    monkeypatch, result, audio_limit, message
):
    provider = FakeProvider(result)
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})
    monkeypatch.setattr(server.settings, "voxbridge_max_audio_bytes", audio_limit)

    with pytest.raises(ToolError, match=message):
        await server.generate_speech("fake", "Hello")


async def test_health_endpoint(monkeypatch):
    monkeypatch.setattr(server, "REGISTRY", {})
    app = server.build_http_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "voxbridge",
        "version": __version__,
    }


async def test_readiness_requires_at_least_one_configured_provider(monkeypatch):
    registry = {
        "zeta": SimpleNamespace(id="zeta", configured=True),
        "alpha": SimpleNamespace(id="alpha", configured=True),
        "off": SimpleNamespace(id="off", configured=False),
    }
    monkeypatch.setattr(server, "REGISTRY", registry)
    app = server.build_http_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "providers": ["alpha", "zeta"]}


async def test_readiness_is_unavailable_without_a_provider(monkeypatch):
    monkeypatch.setattr(
        server,
        "REGISTRY",
        {"off": SimpleNamespace(id="off", configured=False)},
    )
    app = server.build_http_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "providers": []}


async def test_http_app_rejects_untrusted_host_and_origin_even_for_remote_bind(monkeypatch):
    monkeypatch.setattr(server.settings, "voxbridge_host", "0.0.0.0")
    app = server.build_http_app()
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client,
    ):
        bad_host = await client.post("/mcp", json={}, headers={"host": "evil.example"})
        bad_origin = await client.post(
            "/mcp",
            json={},
            headers={"origin": "https://evil.example"},
        )
        allowed_loopback = await client.post(
            "/mcp",
            json={},
            headers={"origin": "http://127.0.0.1:8000"},
        )

    assert bad_host.status_code == 421
    assert bad_origin.status_code == 403
    assert allowed_loopback.status_code not in {403, 421}


async def test_http_app_accepts_explicit_private_proxy_headers(monkeypatch):
    monkeypatch.setattr(server.settings, "voxbridge_host", "0.0.0.0")
    monkeypatch.setattr(server.settings, "voxbridge_allowed_hosts", ["gateway.internal:*"])
    monkeypatch.setattr(
        server.settings,
        "voxbridge_allowed_origins",
        ["https://gateway.internal:*"],
    )
    app = server.build_http_app()
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://gateway.internal:9000") as client,
    ):
        response = await client.post(
            "/mcp",
            json={},
            headers={"origin": "https://gateway.internal:9000"},
        )

    assert response.status_code not in {403, 421}


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10", "gateway.internal"])
def test_non_loopback_bind_fails_closed_without_explicit_override(host):
    configured = Settings(_env_file=None, voxbridge_host=host)

    with pytest.raises(RuntimeError, match="Refusing a non-loopback listener"):
        configured.assert_safe_bind()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_bind_is_allowed(host):
    Settings(_env_file=None, voxbridge_host=host).assert_safe_bind()


def test_non_loopback_bind_requires_explicit_override():
    configured = Settings(
        _env_file=None,
        voxbridge_host="0.0.0.0",
        voxbridge_allow_remote_bind=True,
    )

    configured.assert_safe_bind()


def test_google_credentials_file_loads_from_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GOOGLE_APPLICATION_CREDENTIALS=credentials-from-dotenv.json\n",
        encoding="utf-8",
    )

    loaded = Settings(_env_file=env_file)

    assert loaded.google_application_credentials == "credentials-from-dotenv.json"


def test_download_cache_must_hold_one_maximum_audio_result():
    with pytest.raises(ValueError, match="VOXBRIDGE_AUDIO_DOWNLOAD_MAX_BYTES"):
        Settings(
            _env_file=None,
            voxbridge_max_audio_bytes=2_048,
            voxbridge_audio_download_max_bytes=1_024,
        )
