from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from voxbridge.models import SpeechRequest
from voxbridge.providers.base import ProviderError
from voxbridge.providers.hume import HumeProvider
from voxbridge.providers.openai_voice import OpenAIVoiceProvider
from voxbridge.providers.resemble import ResembleProvider

PRE_SUNSET = datetime(2026, 11, 12, 12, tzinfo=UTC)
POST_SUNSET = datetime(2026, 11, 13, 5, 1, tzinfo=UTC)


async def install_transport(provider, handler) -> None:
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_hume_voice_listing_interleaves_custom_voices_and_deduplicates_ids():
    provider = HumeProvider("hume-secret", now=lambda: PRE_SUNSET)
    requested_sources: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        source = request.url.params["provider"]
        requested_sources.append(source)
        assert request.url.params["page_size"] == "3"
        if source == "HUME_AI":
            voices = [
                {"id": "shared", "name": "Shared stock", "provider": source},
                {"id": "stock-2", "name": "Stock 2", "provider": source},
                {"id": "stock-3", "name": "Stock 3", "provider": source},
            ]
        else:
            voices = [
                {"id": "shared", "name": "Shared custom", "provider": source},
                {"id": "custom-2", "name": "Custom 2", "provider": source},
            ]
        return httpx.Response(200, json={"voices_page": voices})

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(limit=3)
    finally:
        await provider.aclose()

    assert requested_sources == ["HUME_AI", "CUSTOM_VOICE"]
    assert [voice.id for voice in voices] == ["shared", "stock-2", "custom-2"]
    assert len({voice.id for voice in voices}) == len(voices) == 3
    assert voices[-1].metadata["voice_provider"] == "CUSTOM_VOICE"


async def test_hume_zero_voice_limit_does_not_call_provider():
    provider = HumeProvider("hume-secret", now=lambda: PRE_SUNSET)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    await install_transport(provider, handler)
    try:
        assert await provider.list_voices(limit=0) == []
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("output_format", "audio", "mime_type"),
    [
        ("wav", b"RIFF-octave-2", "audio/wav"),
        ("pcm", b"\x01\x02\x03\x04", "audio/L16"),
    ],
)
async def test_hume_octave_2_wav_and_pcm_response_contract(output_format, audio, mime_type):
    provider = HumeProvider("hume-secret", now=lambda: PRE_SUNSET)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://api.hume.ai/v0/tts")
        assert request.headers["x-hume-api-key"] == "hume-secret"
        payload = json.loads(request.read())
        assert payload["format"] == {"type": output_format}
        assert payload["version"] == "2"
        return httpx.Response(
            200,
            json={
                "request_id": "hume-octave-2-request",
                "generations": [
                    {
                        "audio": base64.b64encode(audio).decode(),
                        "encoding": {"format": output_format},
                        "generation_id": "generation-octave-2",
                    }
                ],
            },
        )

    await install_transport(provider, handler)
    try:
        result = await provider.generate(
            SpeechRequest(
                text="Hello",
                voice_id="voice-1",
                model="octave-2",
                output_format=output_format,
            )
        )
    finally:
        await provider.aclose()

    assert result.audio == audio
    assert result.mime_type == mime_type
    assert result.model == "octave-2"
    assert result.request_id == "hume-octave-2-request"


async def test_hume_rejects_provider_calls_at_announced_service_end():
    provider = HumeProvider("hume-secret", now=lambda: POST_SUNSET)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    await install_transport(provider, handler)
    try:
        with pytest.raises(ProviderError, match="reached its announced service end"):
            await provider.list_voices(limit=1)
        with pytest.raises(ProviderError, match="reached its announced service end"):
            await provider.generate(SpeechRequest(text="Hello", voice_id="voice-1"))
    finally:
        await provider.aclose()


async def test_legacy_provider_lifecycle_metadata_is_truthful_and_non_secret():
    hume = HumeProvider(None, now=lambda: PRE_SUNSET)
    resemble = ResembleProvider(None)
    openai = OpenAIVoiceProvider(None)
    try:
        assert hume.info()["control_notes"] == {
            "lifecycle_status": "scheduled_shutdown",
            "service_end": "Hume TTS and EVI are scheduled to end on 2026-11-13 at 00:01 EST.",
        }
        assert resemble.info()["control_notes"]["lifecycle_status"] == "legacy_only"
        assert "historical accounts" in resemble.info()["control_notes"]["hosted_generation_access"]
        assert openai.info()["control_notes"]["lifecycle_status"] == "scheduled_retirement"
        assert "2027-01-06" in openai.info()["control_notes"]["endpoint_retirement"]
        assert all(
            "secret" not in str(provider.info()).lower() for provider in (hume, resemble, openai)
        )
    finally:
        for provider in (hume, resemble, openai):
            await provider.aclose()
