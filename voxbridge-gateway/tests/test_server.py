from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import struct
import wave
from types import SimpleNamespace

import httpx
import pytest
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import AudioContent, EmbeddedResource, ResourceLink, TextContent
from mcp_types import LATEST_PROTOCOL_VERSION
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

from voxbridge import __version__, server
from voxbridge.config import Settings
from voxbridge.models import DialogueSegment, SpeechResult, Voice
from voxbridge.providers.base import ProviderError


class FakeProvider:
    id = "fake"
    display_name = "Fake Voice"
    configured = True
    default_model = "fake-model"

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


def _pcm_wav(
    samples: list[int],
    *,
    channels: int = 1,
    sample_width: int = 2,
    frame_rate: int = 1_000,
) -> bytes:
    if sample_width != 2:
        raise AssertionError("test helper only supports 16-bit PCM")
    frames = struct.pack(f"<{len(samples)}h", *samples)
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(sample_width)
        destination.setframerate(frame_rate)
        destination.writeframes(frames)
    return output.getvalue()


def _wav_samples(audio: bytes) -> tuple[wave._wave_params, list[int]]:
    with wave.open(io.BytesIO(audio), "rb") as source:
        parameters = source.getparams()
        frames = source.readframes(source.getnframes())
    return parameters, list(struct.unpack(f"<{len(frames) // 2}h", frames))


class DialogueProvider(FakeProvider):
    def __init__(self, audio_by_voice: dict[str, bytes]) -> None:
        super().__init__()
        self.audio_by_voice = audio_by_voice

    async def generate(self, request) -> SpeechResult:
        self.generated.append(request)
        return SpeechResult(
            audio=self.audio_by_voice[request.voice_id],
            mime_type="audio/wav",
            provider=self.id,
            model=request.model or "fake-model",
            request_id=f"request-{request.voice_id}",
        )


def test_dialogue_segment_normalizes_and_rejects_voice_ids() -> None:
    assert DialogueSegment(text="Hello", voice_id="  voice-1  ").voice_id == "voice-1"
    with pytest.raises(ValueError, match="voice_id cannot be blank"):
        DialogueSegment(text="Hello", voice_id="   ")


@pytest.fixture(autouse=True)
def clear_audio_artifacts():
    server._generation_replays.clear()
    server._audio_artifacts.clear()
    yield
    server._generation_replays.clear()
    server._audio_artifacts.clear()


async def test_generate_speech_returns_metadata_and_app_playback_file(
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

    assert len(output.content) == 2
    assert isinstance(output.content[0], TextContent)
    metadata = json.loads(output.content[0].text)
    assert metadata == {
        "synthetic_audio": True,
        "provider": "fake",
        "model": "fake-model",
        "mime_type": "audio/wav",
        "request_id": "fake-request",
        "duration": 1.25,
        "delivery": "both",
        "playback_requested": True,
        "inline_audio_included": False,
        "app_resource_playback": True,
        "file_resource_included": True,
        "file_name": metadata["file_name"],
        "file_mime_type": "audio/wav",
        "file_size_bytes": len(b"fake-audio"),
        "sha256": hashlib.sha256(b"fake-audio").hexdigest(),
        "resource_uri": metadata["resource_uri"],
        "materialize_resource_uri": metadata["materialize_resource_uri"],
        "materialize_max_bytes": server.settings.voxbridge_max_materialized_audio_bytes,
        "download_expires_at": metadata["download_expires_at"],
    }
    assert re.fullmatch(r"voxbridge-[0-9a-f]{32}\.wav", metadata["file_name"])
    assert metadata["resource_uri"].endswith(f"/{metadata['file_name']}")
    file_id = metadata["file_name"].removeprefix("voxbridge-").removesuffix(".wav")
    assert metadata["materialize_resource_uri"] == f"voxbridge://audio/{file_id}"
    assert metadata["download_expires_at"].endswith("Z")
    assert isinstance(output.content[1], ResourceLink)
    assert output.content[1].uri == metadata["resource_uri"]
    assert output.content[1].name == metadata["file_name"]
    assert output.content[1].mime_type == "audio/wav"
    assert output.content[1].size == len(b"fake-audio")
    assert output.meta == {
        "voxbridge/audio": {
            "data": base64.b64encode(b"fake-audio").decode("ascii"),
            "mimeType": "audio/wav",
        }
    }
    assert output.structured_content == metadata


async def test_generate_speech_reuses_normalized_replay_within_openai_session(
    monkeypatch,
):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})
    session_a = SimpleNamespace(
        request_context=SimpleNamespace(meta={"openai/session": "session-a"})
    )
    session_b = SimpleNamespace(
        request_context=SimpleNamespace(meta={"openai/session": "session-b"})
    )

    first = await server.generate_speech(
        " FAKE ",
        "Approval replay",
        output_format=" WAV ",
        options_json='{"stability": 0.4, "similarity": 0.8}',
        ctx=session_a,
    )
    replay = await server.generate_speech(
        "fake",
        "Approval replay",
        output_format="wav",
        options_json='{"similarity":0.8,"stability":0.4}',
        ctx=session_a,
    )

    assert len(provider.generated) == 1
    assert replay is first
    assert (
        json.loads(replay.content[0].text)["file_name"]
        == json.loads(first.content[0].text)["file_name"]
    )

    await server.generate_speech(
        "fake",
        "Approval replay",
        output_format="wav",
        options_json='{"similarity":0.8,"stability":0.4}',
        ctx=session_b,
    )
    await server.generate_speech(
        "fake",
        "Different text",
        output_format="wav",
        options_json='{"similarity":0.8,"stability":0.4}',
        ctx=session_a,
    )

    assert len(provider.generated) == 3


async def test_generate_speech_coalesces_concurrent_identical_calls(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})
    started = asyncio.Event()
    release = asyncio.Event()

    async def generate_after_release(request):
        provider.generated.append(request)
        started.set()
        await release.wait()
        return provider.result

    monkeypatch.setattr(provider, "generate", generate_after_release)
    first = asyncio.create_task(server.generate_speech("fake", "Concurrent replay"))
    await started.wait()
    second = asyncio.create_task(server.generate_speech("fake", "Concurrent replay"))
    await asyncio.sleep(0)
    release.set()

    first_output, second_output = await asyncio.gather(first, second)
    assert first_output is second_output
    assert len(provider.generated) == 1


async def test_generate_speech_does_not_cache_provider_failures(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})
    attempts = 0

    async def fail_once(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderError("temporary provider failure")
        provider.generated.append(request)
        return provider.result

    monkeypatch.setattr(provider, "generate", fail_once)

    with pytest.raises(ToolError, match="temporary provider failure"):
        await server.generate_speech("fake", "Retry after failure")

    recovered = await server.generate_speech("fake", "Retry after failure")
    assert recovered.is_error is False
    assert attempts == 2
    assert len(provider.generated) == 1


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
    metadata = json.loads(output.content[0].text)
    link = output.content[1]
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
                "materialize_max_bytes": -1,
            },
        )
    )
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})
    monkeypatch.setattr(server.settings, "voxbridge_max_materialized_audio_bytes", 12_345)

    output = await server.generate_speech("fake", "private script text")
    metadata = json.loads(output.content[0].text)

    assert re.fullmatch(r"voxbridge-[0-9a-f]{32}\.mp3", metadata["file_name"])
    assert metadata["file_size_bytes"] == len(b"safe-audio")
    assert metadata["file_resource_included"] is True
    assert metadata["materialize_max_bytes"] == 12_345
    assert "private" not in metadata["file_name"]
    assert output.content[1].uri == metadata["resource_uri"]


@pytest.mark.parametrize(
    ("delivery", "content_types", "playback_requested", "file_included"),
    [
        ("playback", ["text", "audio"], True, False),
        ("file", ["text", "resource_link"], False, True),
        ("both", ["text", "resource_link"], True, True),
    ],
)
async def test_delivery_selects_playback_file_or_both(
    monkeypatch,
    delivery,
    content_types,
    playback_requested,
    file_included,
):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.generate_speech("fake", "Hello", delivery=delivery)
    metadata = json.loads(output.content[0].text)

    assert [item.type for item in output.content] == content_types
    assert metadata["delivery"] == delivery
    assert metadata["playback_requested"] is playback_requested
    assert metadata["inline_audio_included"] is (delivery == "playback")
    assert metadata["app_resource_playback"] is (delivery == "both")
    assert metadata["file_resource_included"] is file_included
    assert len(provider.generated) == 1
    assert server._audio_artifacts.item_count == int(file_included)
    if delivery == "playback":
        assert isinstance(output.content[1], AudioContent)
        assert output.content[1].mime_type == "audio/wav"
        assert base64.b64decode(output.content[1].data) == b"fake-audio"
    if delivery == "both":
        assert output.meta == {
            "voxbridge/audio": {
                "data": base64.b64encode(b"fake-audio").decode("ascii"),
                "mimeType": "audio/wav",
            }
        }
    else:
        assert output.meta is None
    if file_included:
        assert metadata["file_name"]
        assert metadata["resource_uri"]
        assert metadata["materialize_resource_uri"]
        assert metadata["materialize_max_bytes"] == (
            server.settings.voxbridge_max_materialized_audio_bytes
        )
        assert metadata["download_expires_at"]
    else:
        assert "file_name" not in metadata
        assert "resource_uri" not in metadata
        assert "materialize_resource_uri" not in metadata
        assert "materialize_max_bytes" not in metadata
        assert "download_expires_at" not in metadata


async def test_generate_dialogue_preserves_voice_order_controls_and_pauses(monkeypatch):
    provider = DialogueProvider(
        {
            "julian": _pcm_wav([101, 102]),
            "ethan": _pcm_wav([201]),
            "kyla": _pcm_wav([301, 302]),
        }
    )
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.generate_dialogue(
        provider=" FAKE ",
        model="dialogue-model",
        language="en-US",
        delivery="both",
        segments=[
            DialogueSegment(
                text="First",
                voice_id="julian",
                instructions="Friendly",
                speed=1.1,
                options={"stability": 0.5},
                pause_after_ms=2,
            ),
            DialogueSegment(
                text="Second",
                voice_id="ethan",
                language="en-GB",
                pause_after_ms=1,
            ),
            DialogueSegment(
                text="Third",
                voice_id="kyla",
                pause_after_ms=3,
            ),
            DialogueSegment(
                text="Fourth",
                voice_id="julian",
                # The final pause is deliberately ignored by the dialogue tool.
                pause_after_ms=9_999,
            ),
        ],
    )

    assert [request.voice_id for request in provider.validated] == [
        "julian",
        "ethan",
        "kyla",
        "julian",
    ]
    assert provider.generated == provider.validated
    assert [request.text for request in provider.generated] == [
        "First",
        "Second",
        "Third",
        "Fourth",
    ]
    assert all(request.output_format == "wav" for request in provider.generated)
    assert all(request.model == "dialogue-model" for request in provider.generated)
    assert [request.language for request in provider.generated] == [
        "en-US",
        "en-GB",
        "en-US",
        "en-US",
    ]
    assert provider.generated[0].instructions == "Friendly"
    assert provider.generated[0].speed == 1.1
    assert provider.generated[0].options == {"stability": 0.5}

    metadata = json.loads(output.content[0].text)
    downloadable = output.content[1]
    assert isinstance(downloadable, ResourceLink)
    assert metadata["dialogue"] is True
    assert metadata["segment_count"] == 4
    assert metadata["provider_call_count"] == 4
    assert metadata["voice_ids"] == ["julian", "ethan", "kyla", "julian"]
    assert metadata["total_characters"] == 22
    assert metadata["pause_total_ms"] == 6
    assert metadata["segment_request_ids"] == [
        "request-julian",
        "request-ethan",
        "request-kyla",
        "request-julian",
    ]
    assert metadata["source_format"] == {
        "channels": 1,
        "sample_width_bytes": 2,
        "sample_rate_hz": 1_000,
    }
    assert metadata["duration_seconds"] == pytest.approx(0.013)
    assert metadata["delivery"] == "both"
    assert metadata["mime_type"] == "audio/wav"
    assert metadata["file_mime_type"] == "audio/wav"
    assert output.meta is not None
    assert output.meta["voxbridge/audio"]["mimeType"] == "audio/wav"

    resource = next(iter(await server.mcp.read_resource(downloadable.uri)))
    assert base64.b64decode(output.meta["voxbridge/audio"]["data"]) == resource.content
    parameters, samples = _wav_samples(resource.content)
    assert parameters.nchannels == 1
    assert parameters.sampwidth == 2
    assert parameters.framerate == 1_000
    assert samples == [101, 102, 0, 0, 201, 0, 301, 302, 0, 0, 0, 101, 102]


async def test_generate_dialogue_reuses_exact_approval_replay(monkeypatch):
    provider = DialogueProvider({"one": _pcm_wav([1]), "two": _pcm_wav([2])})
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})
    segments = [
        DialogueSegment(text="One", voice_id="one", pause_after_ms=0),
        DialogueSegment(text="Two", voice_id="two", pause_after_ms=0),
    ]

    first = await server.generate_dialogue("fake", segments, delivery="both")
    replay = await server.generate_dialogue("fake", segments, delivery="both")

    assert replay is first
    assert [request.voice_id for request in provider.generated] == ["one", "two"]

    await server.generate_dialogue("fake", segments, delivery="file")
    assert [request.voice_id for request in provider.generated] == ["one", "two", "one", "two"]


@pytest.mark.parametrize(
    ("delivery", "content_types", "has_file", "has_app_audio"),
    [
        ("playback", ["text", "audio"], False, False),
        ("file", ["text", "resource_link"], True, False),
        ("both", ["text", "resource_link"], True, True),
    ],
)
async def test_generate_dialogue_supports_all_delivery_modes(
    monkeypatch,
    delivery,
    content_types,
    has_file,
    has_app_audio,
):
    provider = DialogueProvider({"one": _pcm_wav([1]), "two": _pcm_wav([2])})
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    output = await server.generate_dialogue(
        "fake",
        [
            DialogueSegment(text="One", voice_id="one", pause_after_ms=0),
            DialogueSegment(text="Two", voice_id="two", pause_after_ms=0),
        ],
        delivery=delivery,
    )

    metadata = json.loads(output.content[0].text)
    assert [item.type for item in output.content] == content_types
    assert metadata["delivery"] == delivery
    assert metadata["file_resource_included"] is has_file
    assert (output.meta is not None) is has_app_audio
    assert server._audio_artifacts.item_count == int(has_file)


async def test_generate_dialogue_rejects_limits_and_bad_options_before_provider_calls(
    monkeypatch,
):
    provider = DialogueProvider({"one": _pcm_wav([1]), "two": _pcm_wav([2])})
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match="at least two"):
        await server.generate_dialogue(
            "fake",
            [DialogueSegment(text="One", voice_id="one")],
        )

    monkeypatch.setattr(server.settings, "voxbridge_max_dialogue_segments", 2)
    with pytest.raises(ToolError, match="2-turn limit"):
        await server.generate_dialogue(
            "fake",
            [
                DialogueSegment(text="One", voice_id="one"),
                DialogueSegment(text="Two", voice_id="two"),
                DialogueSegment(text="Three", voice_id="one"),
            ],
        )

    monkeypatch.setattr(server.settings, "voxbridge_max_dialogue_chars", 5)
    with pytest.raises(ToolError, match="5-character limit"):
        await server.generate_dialogue(
            "fake",
            [
                DialogueSegment(text="One", voice_id="one"),
                DialogueSegment(text="Two", voice_id="two"),
            ],
        )

    monkeypatch.setattr(server.settings, "voxbridge_max_dialogue_chars", 100)
    monkeypatch.setattr(server.settings, "voxbridge_max_dialogue_pause_ms", 5)
    with pytest.raises(ToolError, match="pause.*5"):
        await server.generate_dialogue(
            "fake",
            [
                DialogueSegment(text="One", voice_id="one", pause_after_ms=6),
                DialogueSegment(text="Two", voice_id="two", pause_after_ms=10_000),
            ],
        )

    original_validate = provider.validate_request

    def reject_bad_segment(request):
        original_validate(request)
        if request.options.get("invalid"):
            raise ProviderError("invalid provider option")

    monkeypatch.setattr(provider, "validate_request", reject_bad_segment)
    monkeypatch.setattr(server.settings, "voxbridge_max_dialogue_pause_ms", 1_000)
    with pytest.raises(ToolError, match="Segment 2 is invalid: invalid provider option"):
        await server.generate_dialogue(
            "fake",
            [
                DialogueSegment(text="One", voice_id="one"),
                DialogueSegment(text="Two", voice_id="two", options={"invalid": True}),
            ],
        )

    assert len(provider.validated) == 2
    assert provider.generated == []


async def test_generate_dialogue_rejects_mismatched_provider_wav_streams(monkeypatch):
    provider = DialogueProvider(
        {
            "one": _pcm_wav([1]),
            "two": _pcm_wav([2], frame_rate=2_000),
        }
    )
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match="different WAV channel, sample-width, or sample-rate"):
        await server.generate_dialogue(
            "fake",
            [
                DialogueSegment(text="One", voice_id="one", pause_after_ms=0),
                DialogueSegment(text="Two", voice_id="two", pause_after_ms=0),
            ],
        )

    assert len(provider.generated) == 2
    assert server._audio_artifacts.item_count == 0


async def test_generate_dialogue_reports_partial_provider_work_without_storing_file(
    monkeypatch,
):
    provider = DialogueProvider({"one": _pcm_wav([1]), "two": _pcm_wav([2])})

    async def fail_second(request):
        provider.generated.append(request)
        if request.voice_id == "two":
            raise ProviderError("second turn failed")
        return SpeechResult(
            audio=provider.audio_by_voice[request.voice_id],
            mime_type="audio/wav",
            provider=provider.id,
            model="fake-model",
            request_id="request-one",
        )

    monkeypatch.setattr(provider, "generate", fail_second)
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(
        ToolError,
        match=(
            r"Segment 2 of 2 failed after 1 provider call\(s\) completed.*"
            r"Provider charges may already apply; no combined audio file was stored"
        ),
    ):
        await server.generate_dialogue(
            "fake",
            [
                DialogueSegment(text="One", voice_id="one", pause_after_ms=0),
                DialogueSegment(text="Two", voice_id="two", pause_after_ms=0),
            ],
            delivery="file",
        )

    assert [request.voice_id for request in provider.generated] == ["one", "two"]
    assert server._audio_artifacts.item_count == 0


async def test_generate_dialogue_sanitizes_unexpected_later_provider_failure(monkeypatch):
    provider = DialogueProvider({"one": _pcm_wav([1]), "two": _pcm_wav([2])})

    async def fail_second_unexpectedly(request):
        provider.generated.append(request)
        if request.voice_id == "two":
            raise RuntimeError("sensitive upstream diagnostic")
        return SpeechResult(
            audio=provider.audio_by_voice[request.voice_id],
            mime_type="audio/wav",
            provider=provider.id,
            request_id="request-one",
        )

    monkeypatch.setattr(provider, "generate", fail_second_unexpectedly)
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError) as captured:
        await server.generate_dialogue(
            "fake",
            [
                DialogueSegment(text="One", voice_id="one", pause_after_ms=0),
                DialogueSegment(text="Two", voice_id="two", pause_after_ms=0),
            ],
            delivery="file",
        )

    message = str(captured.value)
    assert "Segment 2 of 2 failed after 1 provider call(s) completed (2 attempted)" in message
    assert "provider adapter failed unexpectedly" in message
    assert "Provider charges may already apply" in message
    assert "sensitive upstream diagnostic" not in message
    assert server._audio_artifacts.item_count == 0


def test_materialize_audio_file_returns_embedded_bytes_and_metadata():
    token, artifact = server._audio_artifacts.put(
        b"materialized-audio",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name=f"voxbridge-{'1' * 32}.mp3",
    )
    resource_uri = server._materialize_resource_uri(artifact.file_name)

    output = server.materialize_audio_file(resource_uri)

    assert [item.type for item in output.content] == ["text", "resource"]
    metadata = json.loads(output.content[0].text)
    assert metadata == {
        "synthetic_audio": True,
        "materialized": True,
        "file_name": f"voxbridge-{'1' * 32}.mp3",
        "file_mime_type": "audio/mpeg",
        "file_size_bytes": len(b"materialized-audio"),
        "sha256": hashlib.sha256(b"materialized-audio").hexdigest(),
        "source_resource_uri": resource_uri,
    }
    assert output.structured_content == metadata
    embedded = output.content[1]
    assert isinstance(embedded, EmbeddedResource)
    assert str(embedded.resource.uri) == f"file:///voxbridge-{'1' * 32}.mp3"
    assert embedded.resource.mime_type == "audio/mpeg"
    assert base64.b64decode(embedded.resource.blob) == b"materialized-audio"

    canonical_uri = server._resource_uri(artifact.format_id, token, artifact.file_name)
    canonical_output = server.materialize_audio_file(canonical_uri)
    canonical_embedded = canonical_output.content[1]
    assert isinstance(canonical_embedded, EmbeddedResource)
    assert base64.b64decode(canonical_embedded.resource.blob) == b"materialized-audio"

    file_name_output = server.materialize_audio_file(file_name=artifact.file_name)
    file_name_metadata = json.loads(file_name_output.content[0].text)
    assert file_name_metadata["source_resource_uri"] == resource_uri
    file_name_embedded = file_name_output.content[1]
    assert isinstance(file_name_embedded, EmbeddedResource)
    assert base64.b64decode(file_name_embedded.resource.blob) == b"materialized-audio"


async def test_mcp_client_receives_materialized_audio_bytes(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    async with Client(server.mcp, mode="auto") as client:
        generated = await client.call_tool(
            "generate_speech",
            {"provider": "fake", "text": "Hello", "delivery": "file"},
        )
        generated_metadata = json.loads(generated.content[0].text)
        resource_uri = generated_metadata["materialize_resource_uri"]
        generated_file_id = (
            generated_metadata["file_name"].removeprefix("voxbridge-").removesuffix(".wav")
        )
        assert resource_uri == f"voxbridge://audio/{generated_file_id}"
        materialized = await client.call_tool(
            "materialize_audio_file",
            {"resource_uri": resource_uri},
        )

    assert generated.is_error is False
    assert materialized.is_error is False
    assert [item.type for item in materialized.content] == ["text", "resource"]
    embedded = materialized.content[1]
    assert isinstance(embedded, EmbeddedResource)
    assert embedded.resource.mime_type == "audio/wav"
    assert base64.b64decode(embedded.resource.blob) == b"fake-audio"


async def test_mcp_client_materializes_by_exact_generated_file_name(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    async with Client(server.mcp, mode="auto") as client:
        generated = await client.call_tool(
            "generate_speech",
            {"provider": "fake", "text": "Hello", "delivery": "file"},
        )
        generated_metadata = json.loads(generated.content[0].text)
        materialized = await client.call_tool(
            "materialize_audio_file",
            {"file_name": generated_metadata["file_name"]},
        )

    assert generated.is_error is False
    assert materialized.is_error is False
    embedded = materialized.content[1]
    assert isinstance(embedded, EmbeddedResource)
    assert embedded.resource.mime_type == "audio/wav"
    assert base64.b64decode(embedded.resource.blob) == b"fake-audio"


@pytest.mark.parametrize(
    ("resource_uri", "file_name"),
    [
        (None, None),
        (f"voxbridge://audio/{'1' * 32}", f"voxbridge-{'1' * 32}.mp3"),
    ],
)
def test_materialize_audio_file_requires_exactly_one_locator(resource_uri, file_name):
    with pytest.raises(ToolError, match="exactly one of resource_uri or file_name"):
        server.materialize_audio_file(resource_uri=resource_uri, file_name=file_name)


@pytest.mark.parametrize(
    "file_name",
    [
        "audio.mp3",
        f"voxbridge-{'A' * 32}.mp3",
        f"voxbridge-{'1' * 32}.exe",
        f"../voxbridge-{'1' * 32}.mp3",
    ],
)
def test_materialize_audio_file_rejects_invalid_generated_file_names(file_name):
    with pytest.raises(ToolError, match="not a valid generated VoxBridge audio file name"):
        server.materialize_audio_file(file_name=file_name)


def test_materialize_audio_file_rejects_unavailable_exact_file_name():
    with pytest.raises(ToolError, match="unavailable or has expired"):
        server.materialize_audio_file(file_name=f"voxbridge-{'0' * 32}.mp3")


@pytest.mark.parametrize(
    "resource_uri",
    [
        "https://example.com/audio.mp3",
        "voxbridge://wrong/mp3/token/file.mp3",
        "voxbridge://audio/mp3/token/file.mp3?query=1",
        "voxbridge://audio/mp3/too",
        "voxbridge://audio/not-a-generated-file-id",
    ],
)
def test_materialize_audio_file_rejects_non_voxbridge_resources(resource_uri):
    with pytest.raises(ToolError, match="not a valid VoxBridge audio resource"):
        server.materialize_audio_file(resource_uri)


def test_materialize_audio_file_rejects_expired_and_oversized_resources(monkeypatch):
    with pytest.raises(ToolError, match="unavailable or has expired"):
        server.materialize_audio_file("voxbridge://audio/mp3/expired/audio.mp3")
    with pytest.raises(ToolError, match="unavailable or has expired"):
        server.materialize_audio_file(f"voxbridge://audio/{'0' * 32}")

    token, artifact = server._audio_artifacts.put(
        b"four",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="voxbridge-safe.mp3",
    )
    monkeypatch.setattr(server.settings, "voxbridge_max_materialized_audio_bytes", 3)

    with pytest.raises(
        ToolError,
        match="too large for inline tool handoff; use Download.*or generate a shorter file",
    ):
        server.materialize_audio_file(
            server._resource_uri(artifact.format_id, token, artifact.file_name)
        )


async def test_invalid_delivery_is_rejected_before_generation(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match="delivery must be"):
        await server.generate_speech("fake", "Hello", delivery="stream")

    assert provider.generated == []


@pytest.mark.parametrize("delivery", ["playback", "file", "both"])
async def test_pcm_delivery_is_rejected_before_generation(monkeypatch, delivery):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    with pytest.raises(ToolError, match="PCM delivery.*use WAV"):
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
async def test_mcp_client_receives_downloadable_file_for_app_playback(monkeypatch, mode):
    provider = FakeProvider()
    monkeypatch.setattr(server, "REGISTRY", {provider.id: provider})

    async with Client(server.mcp, mode=mode) as client:
        result = await client.call_tool(
            "generate_speech",
            {"provider": "fake", "text": "Hello"},
        )

        assert result.is_error is False
        assert [item.type for item in result.content] == ["text", "resource_link"]
        metadata = json.loads(result.content[0].text)
        downloadable = result.content[1]
        playback_resource = await client.read_resource(downloadable.uri, cache_mode="bypass")
        download_resource = await client.read_resource(downloadable.uri, cache_mode="bypass")

    assert result.meta is not None
    assert result.meta["voxbridge/audio"] == {
        "data": base64.b64encode(b"fake-audio").decode("ascii"),
        "mimeType": "audio/wav",
    }
    assert downloadable.uri == metadata["resource_uri"]
    assert downloadable.name == metadata["file_name"]
    assert downloadable.mime_type == "audio/wav"
    for resource in (playback_resource, download_resource):
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
        "generate_dialogue",
        "materialize_audio_file",
    }
    by_name = {tool.name: tool for tool in result.tools}
    assert by_name["list_providers"].annotations.read_only_hint is True
    assert by_name["list_voices"].annotations.read_only_hint is True
    assert by_name["generate_speech"].annotations.read_only_hint is False
    assert by_name["generate_dialogue"].annotations.read_only_hint is False
    assert by_name["materialize_audio_file"].annotations.read_only_hint is True
    assert by_name["materialize_audio_file"].annotations.open_world_hint is False
    assert set(by_name["generate_speech"].input_schema["required"]) == {"provider", "text"}
    assert "ctx" not in by_name["generate_speech"].input_schema["properties"]
    delivery_schema = by_name["generate_speech"].input_schema["properties"]["delivery"]
    assert delivery_schema["default"] == "both"
    assert set(delivery_schema["enum"]) == {"playback", "file", "both"}
    generate_meta = by_name["generate_speech"].meta
    delivery_output_schema = by_name["generate_speech"].output_schema
    assert delivery_output_schema is not None
    assert {
        "synthetic_audio",
        "provider",
        "model",
        "mime_type",
        "request_id",
        "delivery",
        "playback_requested",
        "inline_audio_included",
        "app_resource_playback",
        "file_resource_included",
        "file_size_bytes",
        "sha256",
    }.issubset(delivery_output_schema["required"])
    assert delivery_output_schema["additionalProperties"] is True
    materialize_limit_schema = delivery_output_schema["properties"]["materialize_max_bytes"]
    assert any(option.get("minimum") == 1 for option in materialize_limit_schema["anyOf"])
    dialogue_schema = by_name["generate_dialogue"].input_schema
    assert "ctx" not in dialogue_schema["properties"]
    assert set(dialogue_schema["required"]) == {"provider", "segments"}
    assert dialogue_schema["properties"]["delivery"]["default"] == "both"
    assert set(dialogue_schema["properties"]["delivery"]["enum"]) == {
        "playback",
        "file",
        "both",
    }
    segment_schema = dialogue_schema["$defs"]["DialogueSegment"]
    assert set(segment_schema["required"]) == {"text", "voice_id"}
    assert segment_schema["properties"]["pause_after_ms"]["default"] == 250
    assert by_name["generate_dialogue"].output_schema == delivery_output_schema
    assert "required" not in by_name["materialize_audio_file"].input_schema
    materialize_uri_schema = by_name["materialize_audio_file"].input_schema["properties"][
        "resource_uri"
    ]
    assert "materialize_resource_uri" in materialize_uri_schema["description"]
    materialize_file_name_schema = by_name["materialize_audio_file"].input_schema["properties"][
        "file_name"
    ]
    assert "exact" in materialize_file_name_schema["description"]
    assert "host" in materialize_file_name_schema["description"]
    materialize_output_schema = by_name["materialize_audio_file"].output_schema
    assert materialize_output_schema is not None
    assert set(materialize_output_schema["required"]) == {
        "synthetic_audio",
        "materialized",
        "file_name",
        "file_mime_type",
        "file_size_bytes",
        "sha256",
        "source_resource_uri",
    }
    assert materialize_output_schema["properties"]["synthetic_audio"]["const"] is True
    assert materialize_output_schema["properties"]["materialized"]["const"] is True
    assert materialize_output_schema["properties"]["file_size_bytes"]["minimum"] == 1
    assert materialize_output_schema["properties"]["sha256"]["pattern"] == "^[a-f0-9]{64}$"
    assert server._AUDIO_DELIVERY_UI_URI == "ui://voxbridge/audio-delivery-v11.html"
    assert generate_meta["ui"]["resourceUri"] == server._AUDIO_DELIVERY_UI_URI
    assert generate_meta["openai/outputTemplate"] == server._AUDIO_DELIVERY_UI_URI
    assert by_name["generate_dialogue"].meta == generate_meta
    materialize_meta = by_name["materialize_audio_file"].meta
    assert materialize_meta["ui"]["visibility"] == ["model", "app"]
    assert materialize_meta["openai/widgetAccessible"] is True
    assert materialize_meta["openai/toolInvocation/invoking"] == ("Adding audio file to ChatGPT…")
    assert materialize_meta["openai/toolInvocation/invoked"] == ("Audio file added to ChatGPT")
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
    assert "callServerTool" in document.text
    assert "serverTools" in document.text
    assert "materialize_audio_file" in document.text
    assert "Add file to ChatGPT" in document.text
    assert "Adding file to ChatGPT" in document.text
    assert "VB-HANDOFF-MATERIALIZE" in document.text
    assert "uploadFile" not in document.text
    assert "Prepare for ChatGPT" not in document.text
    assert "Save to ChatGPT" not in document.text
    assert "Upload to ChatGPT" not in document.text
    assert "toolResponseMetadata" in document.text
    assert "button[hidden]" in document.text
    assert 'id="play-pause"' in document.text
    assert "AudioContext" in document.text
    assert "decodeAudioData" in document.text
    assert "createBufferSource" in document.text
    assert "URL.createObjectURL" not in document.text
    assert "media-src blob:" not in document.text
    assert "Ask ChatGPT to retrieve the existing VoxBridge file" in document.text
    assert "host omits a native Download control" in document.text
    assert "voxbridge/audio" in document.text
    assert "Choose Play audio to load the file for playback" in document.text
    assert "If file approval is pending" in document.text


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


def test_materialized_audio_limit_cannot_exceed_generation_limit():
    with pytest.raises(ValueError, match="VOXBRIDGE_MAX_MATERIALIZED_AUDIO_BYTES"):
        Settings(
            _env_file=None,
            voxbridge_max_audio_bytes=2_048,
            voxbridge_max_materialized_audio_bytes=4_096,
            voxbridge_audio_download_max_bytes=4_096,
        )
