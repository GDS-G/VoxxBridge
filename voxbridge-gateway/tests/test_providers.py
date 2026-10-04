from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, call

import httpx
import pytest

from voxbridge.models import MusicRequest, SoundEffectRequest, SpeechRequest
from voxbridge.providers.azure_speech import AzureSpeechProvider
from voxbridge.providers.base import ProviderError
from voxbridge.providers.cartesia import CartesiaProvider
from voxbridge.providers.deepgram import DeepgramProvider
from voxbridge.providers.elevenlabs import ElevenLabsProvider
from voxbridge.providers.google_cloud import GoogleCloudProvider
from voxbridge.providers.google_lyria import GoogleLyriaProvider
from voxbridge.providers.hume import HumeProvider
from voxbridge.providers.openai_voice import OpenAIVoiceProvider
from voxbridge.providers.resemble import ResembleProvider
from voxbridge.providers.stability import StabilityAudioProvider


async def install_transport(provider, handler) -> None:
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


async def test_elevenlabs_voice_and_synthesis_contracts():
    provider = ElevenLabsProvider("eleven-secret")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.headers["xi-api-key"] == "eleven-secret"
        if request.method == "GET":
            assert request.url == httpx.URL(
                "https://api.elevenlabs.io/v2/voices?page_size=2&language=fr"
            )
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "voice_id": "voice-1",
                            "name": "Camille",
                            "verified_languages": [{"language": "fr"}],
                            "labels": {"gender": "female"},
                            "description": "Warm",
                            "preview_url": "https://example.test/preview.mp3",
                            "category": "premade",
                        }
                    ]
                },
            )

        assert request.url == httpx.URL(
            "https://api.elevenlabs.io/v1/text-to-speech/voice-1?output_format=wav_24000"
        )
        assert request.headers["content-type"] == "application/json"
        assert body(request) == {
            "text": "Bonjour",
            "model_id": "model-1",
            "language_code": "fr",
            "voice_settings": {
                "speed": 1.2,
                "stability": 0.4,
                "similarity_boost": 0.8,
                "style": 0.3,
                "use_speaker_boost": True,
            },
        }
        return httpx.Response(
            200,
            content=b"RIFF-eleven",
            headers={
                "content-type": "audio/wav; charset=binary",
                "request-id": "eleven-request",
                "character-cost": "7",
            },
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(language="fr", limit=2)
        result = await provider.generate(
            SpeechRequest(
                text="Bonjour",
                voice_id="voice-1",
                model="model-1",
                output_format="wav",
                language="fr",
                speed=1.2,
                options={
                    "stability": 0.4,
                    "similarity_boost": 0.8,
                    "style": 0.3,
                    "use_speaker_boost": True,
                },
            )
        )
    finally:
        await provider.aclose()

    assert calls == ["GET", "POST"]
    assert [(voice.id, voice.name, voice.language) for voice in voices] == [
        ("voice-1", "Camille", "fr")
    ]
    assert voices[0].metadata == {
        "category": "premade",
        "labels": {"gender": "female"},
    }
    assert result.audio == b"RIFF-eleven"
    assert result.mime_type == "audio/wav"
    assert result.model == "model-1"
    assert result.request_id == "eleven-request"
    assert result.metadata == {"character_cost": "7"}


async def test_elevenlabs_music_prompt_contract_and_result_metadata():
    provider = ElevenLabsProvider(
        "eleven-secret",
        music_timeout=123.0,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL("https://api.elevenlabs.io/v1/music?output_format=auto")
        assert request.headers["xi-api-key"] == "eleven-secret"
        assert request.headers["content-type"] == "application/json"
        assert body(request) == {
            "model_id": "music_v2_5",
            "sign_with_c2pa": True,
            "prompt": "A bright three-second ident with a clean ending",
            "music_length_ms": 3_000,
            "force_instrumental": True,
            "finetune_id": "brand-finetune",
        }
        assert request.extensions["timeout"] == {
            "connect": 123.0,
            "read": 123.0,
            "write": 123.0,
            "pool": 123.0,
        }
        return httpx.Response(
            200,
            content=b"ID3-eleven-music",
            headers={
                "content-type": "audio/mpeg; charset=binary",
                "request-id": "music-request-1",
                "song-id": "song-1",
            },
        )

    await install_transport(provider, handler)
    try:
        result = await provider.generate_music(
            MusicRequest(
                prompt="A bright three-second ident with a clean ending",
                music_length_ms=3_000,
                model="music_v2_5",
                force_instrumental=True,
                finetune_id="  brand-finetune  ",
                sign_with_c2pa=True,
            )
        )
    finally:
        await provider.aclose()

    assert result.audio == b"ID3-eleven-music"
    assert result.mime_type == "audio/mpeg"
    assert result.provider == "elevenlabs"
    assert result.model == "music_v2_5"
    assert result.request_id == "music-request-1"
    assert result.metadata == {
        "music": True,
        "music_mode": "prompt",
        "music_length_ms": 3_000,
        "force_instrumental": True,
        "song_id": "song-1",
    }


async def test_elevenlabs_music_composition_plan_contract():
    provider = ElevenLabsProvider("eleven-secret")
    plan = {
        "positive_global_styles": ["cinematic", "warm"],
        "negative_global_styles": ["vocals"],
        "sections": [
            {
                "section_name": "Intro",
                "positive_local_styles": ["piano"],
                "negative_local_styles": [],
                "duration_ms": 3_000,
                "lines": [],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL("https://api.elevenlabs.io/v1/music?output_format=auto")
        assert request.headers["xi-api-key"] == "eleven-secret"
        assert request.headers["content-type"] == "application/json"
        assert body(request) == {
            "model_id": "music_v2",
            "sign_with_c2pa": False,
            "composition_plan": plan,
            "respect_sections_durations": False,
            "seed": 2_147_483_647,
            "finetune_id": "plan-finetune",
        }
        return httpx.Response(
            200,
            content=b"ID3-planned-music",
            headers={"content-type": "application/octet-stream"},
        )

    await install_transport(provider, handler)
    try:
        result = await provider.generate_music(
            MusicRequest(
                composition_plan=plan,
                model="music_v2",
                seed=2_147_483_647,
                finetune_id="plan-finetune",
                respect_sections_durations=False,
            )
        )
    finally:
        await provider.aclose()

    assert result.audio == b"ID3-planned-music"
    assert result.mime_type == "audio/mpeg"
    assert result.model == "music_v2"
    assert result.request_id is None
    assert result.metadata == {
        "music": True,
        "music_mode": "composition_plan",
        "music_length_ms": None,
        "force_instrumental": None,
        "song_id": None,
    }


@pytest.mark.parametrize(
    ("music_request", "message"),
    [
        (
            MusicRequest(),
            "ElevenLabs music requires exactly one of prompt or composition_plan",
        ),
        (
            MusicRequest(prompt="Song", composition_plan={"sections": []}),
            "ElevenLabs music requires exactly one of prompt or composition_plan",
        ),
        (
            MusicRequest(prompt="   "),
            "ElevenLabs music prompt cannot be empty",
        ),
        (
            MusicRequest(prompt="x" * 4_101),
            "ElevenLabs music prompt accepts at most 4,100 characters",
        ),
        (
            MusicRequest(composition_plan={}),
            "ElevenLabs composition_plan must be a non-empty object",
        ),
        (
            MusicRequest(composition_plan=[{"sections": []}]),
            "ElevenLabs composition_plan must be a non-empty object",
        ),
        (
            MusicRequest(prompt="Song", model="music_v3"),
            "ElevenLabs music model must be music_v1, music_v2, or music_v2_5",
        ),
        (
            MusicRequest(prompt="Song", output_format="wav"),
            "ElevenLabs music output must be mp3",
        ),
        (
            MusicRequest(prompt="Song", music_length_ms=True),
            "ElevenLabs music_length_ms must be an integer",
        ),
        (
            MusicRequest(prompt="Song", music_length_ms=2_999),
            "ElevenLabs music_length_ms must be between 3000 and 600000",
        ),
        (
            MusicRequest(prompt="Song", music_length_ms=600_001),
            "ElevenLabs music_length_ms must be between 3000 and 600000",
        ),
        (
            MusicRequest(
                composition_plan={"sections": []},
                music_length_ms=3_000,
            ),
            "ElevenLabs music_length_ms can only be used with a prompt",
        ),
        (
            MusicRequest(prompt="Song", force_instrumental="yes"),
            "ElevenLabs force_instrumental must be a boolean",
        ),
        (
            MusicRequest(
                composition_plan={"sections": []},
                force_instrumental=True,
            ),
            "ElevenLabs force_instrumental can only be used with a prompt",
        ),
        (
            MusicRequest(composition_plan={"sections": []}, seed=True),
            "ElevenLabs music seed must be an integer",
        ),
        (
            MusicRequest(composition_plan={"sections": []}, seed=-1),
            "ElevenLabs music seed must be between 0 and 2147483647",
        ),
        (
            MusicRequest(
                composition_plan={"sections": []},
                seed=2_147_483_648,
            ),
            "ElevenLabs music seed must be between 0 and 2147483647",
        ),
        (
            MusicRequest(prompt="Song", seed=1),
            "ElevenLabs music seed cannot be used with a prompt",
        ),
        (
            MusicRequest(prompt="Song", finetune_id=""),
            "ElevenLabs music finetune_id must contain 1 to 100 characters",
        ),
        (
            MusicRequest(prompt="Song", finetune_id="x" * 101),
            "ElevenLabs music finetune_id must contain 1 to 100 characters",
        ),
        (
            MusicRequest(prompt="Song", respect_sections_durations=1),
            "ElevenLabs respect_sections_durations must be a boolean",
        ),
        (
            MusicRequest(prompt="Song", sign_with_c2pa=1),
            "ElevenLabs sign_with_c2pa must be a boolean",
        ),
    ],
)
async def test_elevenlabs_music_rejects_invalid_requests_before_http(music_request, message):
    provider = ElevenLabsProvider("key")
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate_music(music_request)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "music_request",
    [
        MusicRequest(prompt="x" * 4_100, music_length_ms=3_000),
        MusicRequest(prompt="Song", music_length_ms=600_000, finetune_id="x" * 100),
        MusicRequest(composition_plan={"sections": []}, seed=0),
        MusicRequest(composition_plan={"sections": []}, seed=2_147_483_647),
    ],
)
async def test_elevenlabs_music_accepts_documented_boundaries(music_request):
    provider = ElevenLabsProvider("key")
    try:
        provider.validate_music_request(music_request)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (
            403,
            "ElevenLabs rejected its credentials or Music API entitlement",
        ),
        (
            422,
            "ElevenLabs rejected the music prompt, composition plan, or settings",
        ),
    ],
)
async def test_elevenlabs_music_returns_safe_provider_errors(status_code, message):
    provider = ElevenLabsProvider("do-not-leak-api-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={
                "detail": "sensitive upstream diagnostic",
                "api_key": "do-not-leak-api-key",
            },
            headers={"request-id": "music-error-request"},
        )

    await install_transport(provider, handler)
    try:
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate_music(MusicRequest(prompt="Safe test"))
    finally:
        await provider.aclose()

    error = exc_info.value
    assert str(error) == message
    assert error.status_code == status_code
    assert error.request_id == "music-error-request"
    assert error.retryable is False
    assert "sensitive upstream diagnostic" not in str(error)
    assert "do-not-leak-api-key" not in str(error)


async def test_elevenlabs_sound_effect_contract_and_result_metadata():
    provider = ElevenLabsProvider(
        "eleven-secret",
        sound_effect_timeout=87.0,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_128"
        )
        assert request.headers["xi-api-key"] == "eleven-secret"
        assert request.headers["content-type"] == "application/json"
        assert body(request) == {
            "text": "Soft rain on a canvas tent with distant thunder",
            "loop": True,
            "prompt_influence": 0.7,
            "model_id": "eleven_text_to_sound_v2",
            "duration_seconds": 6.5,
        }
        assert request.extensions["timeout"] == {
            "connect": 87.0,
            "read": 87.0,
            "write": 87.0,
            "pool": 87.0,
        }
        return httpx.Response(
            200,
            content=b"ID3-eleven-sound-effect",
            headers={
                "content-type": "audio/mpeg; charset=binary",
                "request-id": "sound-effect-request-1",
                "character-cost": "260",
            },
        )

    await install_transport(provider, handler)
    try:
        result = await provider.generate_sound_effect(
            SoundEffectRequest(
                prompt="Soft rain on a canvas tent with distant thunder",
                duration_seconds=6.5,
                loop=True,
                prompt_influence=0.7,
            )
        )
    finally:
        await provider.aclose()

    assert result.audio == b"ID3-eleven-sound-effect"
    assert result.mime_type == "audio/mpeg"
    assert result.provider == "elevenlabs"
    assert result.model == "eleven_text_to_sound_v2"
    assert result.request_id == "sound-effect-request-1"
    assert result.metadata == {
        "sound_effect": True,
        "duration_seconds": 6.5,
        "loop": True,
        "prompt_influence": 0.7,
        "character_cost": "260",
    }


async def test_elevenlabs_sound_effect_omits_auto_duration():
    provider = ElevenLabsProvider("eleven-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert body(request) == {
            "text": "A single wooden knock",
            "loop": False,
            "prompt_influence": 0.3,
            "model_id": "eleven_text_to_sound_v2",
        }
        return httpx.Response(200, content=b"ID3-knock")

    await install_transport(provider, handler)
    try:
        result = await provider.generate_sound_effect(
            SoundEffectRequest(prompt="A single wooden knock")
        )
    finally:
        await provider.aclose()

    assert result.metadata["duration_seconds"] is None


async def test_elevenlabs_sound_effect_rejects_non_audio_success_response():
    provider = ElevenLabsProvider("eleven-secret")

    await install_transport(
        provider,
        lambda request: httpx.Response(
            200,
            json={"error": "unexpected success envelope"},
            headers={"content-type": "application/json"},
        ),
    )
    try:
        with pytest.raises(ProviderError, match="invalid sound-effect audio response"):
            await provider.generate_sound_effect(SoundEffectRequest(prompt="A short knock"))
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("sound_effect_request", "message"),
    [
        (
            SoundEffectRequest(prompt="   "),
            "ElevenLabs sound-effect prompt cannot be empty",
        ),
        (
            SoundEffectRequest(prompt="x" * 451),
            "ElevenLabs sound-effect prompt accepts at most 450 characters",
        ),
        (
            SoundEffectRequest(prompt="Knock", model="sound-v3"),
            "ElevenLabs sound-effect model must be eleven_text_to_sound_v2",
        ),
        (
            SoundEffectRequest(prompt="Knock", output_format="wav"),
            "ElevenLabs sound-effect output must be mp3",
        ),
        (
            SoundEffectRequest(prompt="Knock", duration_seconds=True),
            "ElevenLabs sound-effect duration_seconds must be a number",
        ),
        (
            SoundEffectRequest(prompt="Knock", duration_seconds=0.49),
            "ElevenLabs sound-effect duration_seconds must be between 0.5 and 30",
        ),
        (
            SoundEffectRequest(prompt="Knock", duration_seconds=30.01),
            "ElevenLabs sound-effect duration_seconds must be between 0.5 and 30",
        ),
        (
            SoundEffectRequest(prompt="Knock", loop="yes"),
            "ElevenLabs sound-effect loop must be a boolean",
        ),
        (
            SoundEffectRequest(prompt="Knock", prompt_influence=True),
            "ElevenLabs sound-effect prompt_influence must be a number between 0 and 1",
        ),
        (
            SoundEffectRequest(prompt="Knock", prompt_influence=-0.01),
            "ElevenLabs sound-effect prompt_influence must be a number between 0 and 1",
        ),
        (
            SoundEffectRequest(prompt="Knock", prompt_influence=1.01),
            "ElevenLabs sound-effect prompt_influence must be a number between 0 and 1",
        ),
    ],
)
async def test_elevenlabs_sound_effect_rejects_invalid_requests_before_http(
    sound_effect_request,
    message,
):
    provider = ElevenLabsProvider("key")
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate_sound_effect(sound_effect_request)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "sound_effect_request",
    [
        SoundEffectRequest(prompt="x" * 450, duration_seconds=0.5, prompt_influence=0.0),
        SoundEffectRequest(prompt="Knock", duration_seconds=30.0, prompt_influence=1.0),
    ],
)
async def test_elevenlabs_sound_effect_accepts_documented_boundaries(sound_effect_request):
    provider = ElevenLabsProvider("key")
    try:
        provider.validate_sound_effect_request(sound_effect_request)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (
            403,
            "ElevenLabs rejected its credentials or Sound Effects API entitlement",
        ),
        (
            422,
            "ElevenLabs rejected the sound-effect prompt or settings",
        ),
    ],
)
async def test_elevenlabs_sound_effect_returns_safe_provider_errors(status_code, message):
    provider = ElevenLabsProvider("do-not-leak-api-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={
                "detail": "sensitive upstream diagnostic",
                "api_key": "do-not-leak-api-key",
            },
            headers={"request-id": "sound-effect-error-request"},
        )

    await install_transport(provider, handler)
    try:
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate_sound_effect(SoundEffectRequest(prompt="Safe test"))
    finally:
        await provider.aclose()

    error = exc_info.value
    assert str(error) == message
    assert error.status_code == status_code
    assert error.request_id == "sound-effect-error-request"
    assert error.retryable is False
    assert "sensitive upstream diagnostic" not in str(error)
    assert "do-not-leak-api-key" not in str(error)


async def test_hume_voice_and_synthesis_contracts():
    provider = HumeProvider("hume-secret")
    sources: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-hume-api-key"] == "hume-secret"
        if request.method == "GET":
            source = request.url.params["provider"]
            sources.append(source)
            assert request.url.params["page_size"] == "2"
            assert request.url.params["filter_tag"] == "LANGUAGE:en"
            suffix = "stock" if source == "HUME_AI" else "custom"
            return httpx.Response(
                200,
                json={
                    "voices_page": [
                        {"id": f"voice-{suffix}", "name": suffix.title(), "provider": source}
                    ]
                },
            )

        assert request.url == httpx.URL("https://api.hume.ai/v0/tts")
        assert body(request) == {
            "utterances": [
                {
                    "text": "Hello",
                    "voice": {"id": "voice-custom"},
                    "description": "Calm and grounded",
                    "speed": 0.9,
                }
            ],
            "format": {"type": "pcm"},
            "num_generations": 1,
            "version": "1",
        }
        return httpx.Response(
            200,
            json={
                "request_id": "hume-request",
                "generations": [
                    {
                        "audio": base64.b64encode(b"hume-pcm").decode(),
                        "encoding": {"format": "pcm"},
                        "generation_id": "generation-1",
                        "duration": 1.5,
                    }
                ],
            },
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(language="en", limit=2)
        result = await provider.generate(
            SpeechRequest(
                text="Hello",
                voice_id="voice-custom",
                model="octave-1",
                output_format="pcm",
                instructions="Calm and grounded",
                speed=0.9,
                options={"num_generations": 1},
            )
        )
    finally:
        await provider.aclose()

    assert sources == ["HUME_AI", "CUSTOM_VOICE"]
    assert [voice.id for voice in voices] == ["voice-stock", "voice-custom"]
    assert [voice.metadata["voice_provider"] for voice in voices] == sources
    assert result.audio == b"hume-pcm"
    assert result.mime_type == "audio/L16"
    assert result.model == "octave-1"
    assert result.request_id == "hume-request"
    assert result.metadata == {"generation_id": "generation-1", "duration": 1.5}


async def test_cartesia_voice_and_synthesis_contracts():
    provider = CartesiaProvider("cartesia-secret", "2026-08-14")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer cartesia-secret"
        assert request.headers["cartesia-version"] == "2026-08-14"
        if request.method == "GET":
            assert request.url == httpx.URL("https://api.cartesia.ai/voices?limit=3&language=es")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "cartesia-1",
                            "name": "Sofia",
                            "languages": ["es", "en"],
                            "description": "Conversational",
                            "gender": "female",
                            "accent": "mx",
                        }
                    ]
                },
            )

        assert request.url == httpx.URL("https://api.cartesia.ai/tts/bytes")
        assert body(request) == {
            "model_id": "sonic-custom",
            "transcript": "Hola",
            "voice": "cartesia-1",
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": 44100,
            },
            "language": "en-US",
            "generation_config": {
                "speed": 1.1,
                "volume": 0.75,
                "emotion": "joking/comedic",
            },
        }
        return httpx.Response(
            200,
            content=b"RIFF-cartesia",
            headers={"content-type": "audio/wav", "x-request-id": "cartesia-request"},
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(language="es", limit=3)
        result = await provider.generate(
            SpeechRequest(
                text="Hola",
                voice_id="cartesia-1",
                model="sonic-custom",
                output_format="wav",
                language="en-US",
                speed=1.1,
                options={"volume": 0.75, "emotion": "joking/comedic"},
            )
        )
    finally:
        await provider.aclose()

    assert [(voice.id, voice.name, voice.language) for voice in voices] == [
        ("cartesia-1", "Sofia", "es")
    ]
    assert voices[0].metadata == {"gender": "female", "accent": "mx"}
    assert result.audio == b"RIFF-cartesia"
    assert result.mime_type == "audio/wav"
    assert result.model == "sonic-custom"
    assert result.request_id == "cartesia-request"


async def test_resemble_voice_and_synthesis_contracts():
    provider = ResembleProvider("resemble-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer resemble-secret"
        if request.method == "GET":
            assert request.url.params == httpx.QueryParams(
                {"page": 1, "page_size": 10, "sample_url": True}
            )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "uuid": "resemble-en",
                            "name": "Alex",
                            "language": "en-US",
                            "sample_url": "https://example.test/alex.wav",
                            "status": "ready",
                        },
                        {"uuid": "resemble-fr", "name": "Amelie", "language": "fr-FR"},
                    ]
                },
            )

        assert request.url == httpx.URL("https://f.cluster.resemble.ai/synthesize")
        assert body(request) == {
            "voice_uuid": "resemble-en",
            "data": "Hello",
            "output_format": "mp3",
            "use_hd": True,
            "sample_rate": 48000,
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "audio_content": base64.b64encode(b"resemble-mp3").decode(),
                "duration": 2.0,
                "sample_rate": 48000,
                "issues": [],
            },
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(language="en", limit=2)
        result = await provider.generate(
            SpeechRequest(
                text="Hello",
                voice_id="resemble-en",
                options={"use_hd": True, "sample_rate": 48000},
            )
        )
    finally:
        await provider.aclose()

    assert [voice.id for voice in voices] == ["resemble-en"]
    assert voices[0].preview_url == "https://example.test/alex.wav"
    assert result.audio == b"resemble-mp3"
    assert result.mime_type == "audio/mpeg"
    assert result.metadata == {"duration": 2.0, "sample_rate": 48000, "issues": []}


async def test_openai_voice_and_synthesis_contracts():
    provider = OpenAIVoiceProvider("openai-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL("https://api.openai.com/v1/audio/speech")
        assert request.headers["authorization"] == "Bearer openai-secret"
        assert body(request) == {
            "model": "gpt-4o-mini-tts",
            "input": "Hello",
            "voice": {"id": "voice_custom"},
            "response_format": "flac",
            "instructions": "Measured and warm",
            "speed": 0.8,
        }
        return httpx.Response(
            200,
            content=b"fLaC-openai",
            headers={"x-request-id": "openai-request"},
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(limit=2)
        result = await provider.generate(
            SpeechRequest(
                text="Hello",
                voice_id="voice_custom",
                output_format="flac",
                instructions="Measured and warm",
                speed=0.8,
            )
        )
    finally:
        await provider.aclose()

    assert [(voice.id, voice.language) for voice in voices] == [
        ("alloy", "multilingual"),
        ("ash", "multilingual"),
    ]
    assert result.audio == b"fLaC-openai"
    assert result.mime_type == "audio/flac"
    assert result.model == "gpt-4o-mini-tts"
    assert result.request_id == "openai-request"


async def test_deepgram_voice_and_synthesis_contracts():
    provider = DeepgramProvider("deepgram-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "https://api.deepgram.com/v1/speak?model=aura-custom-en&encoding=linear16&container=wav"
        )
        assert request.headers["authorization"] == "Token deepgram-secret"
        assert body(request) == {"text": "Hello"}
        return httpx.Response(
            200,
            content=b"RIFF-deepgram",
            headers={"content-type": "audio/wav", "dg-request-id": "deepgram-request"},
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(language="en", limit=2)
        result = await provider.generate(
            SpeechRequest(text="Hello", model="aura-custom-en", output_format="wav")
        )
    finally:
        await provider.aclose()

    assert [voice.id for voice in voices] == ["aura-2-thalia-en", "aura-2-andromeda-en"]
    assert all(voice.language == "en" for voice in voices)
    assert result.audio == b"RIFF-deepgram"
    assert result.mime_type == "audio/wav"
    assert result.model == "aura-custom-en"
    assert result.request_id == "deepgram-request"


async def test_google_voice_and_synthesis_contracts(monkeypatch):
    provider = GoogleCloudProvider("project-1")
    auth_headers = AsyncMock(
        return_value={
            "Authorization": "Bearer google-token",
            "x-goog-user-project": "project-1",
        }
    )
    monkeypatch.setattr(provider, "_auth_headers", auth_headers)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer google-token"
        assert request.headers["x-goog-user-project"] == "project-1"
        if request.method == "GET":
            assert request.url == httpx.URL(
                "https://texttospeech.googleapis.com/v1/voices?languageCode=de-DE"
            )
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {
                            "name": "de-DE-Neural2-A",
                            "languageCodes": ["de-DE", "de"],
                            "ssmlGender": "FEMALE",
                            "naturalSampleRateHertz": 24000,
                        }
                    ]
                },
            )

        assert request.url == httpx.URL("https://texttospeech.googleapis.com/v1/text:synthesize")
        assert body(request) == {
            "input": {"text": "Guten Tag"},
            "voice": {"languageCode": "de-DE", "name": "de-DE-Neural2-A"},
            "audioConfig": {"audioEncoding": "OGG_OPUS", "speakingRate": 1.25},
        }
        return httpx.Response(
            200,
            json={"audioContent": base64.b64encode(b"ogg-google").decode()},
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(language="de-DE", limit=1)
        result = await provider.generate(
            SpeechRequest(
                text="Guten Tag",
                voice_id="de-DE-Neural2-A",
                output_format="opus",
                language="de-DE",
                speed=1.25,
            )
        )
    finally:
        await provider.aclose()

    assert auth_headers.await_args_list == [
        call("GET", "https://texttospeech.googleapis.com/v1/voices"),
        call("POST", "https://texttospeech.googleapis.com/v1/text:synthesize"),
    ]
    assert [(voice.id, voice.language, voice.gender) for voice in voices] == [
        ("de-DE-Neural2-A", "de-DE", "FEMALE")
    ]
    assert voices[0].metadata == {
        "sample_rate_hz": 24000,
        "language_codes": ["de-DE", "de"],
    }
    assert result.audio == b"ogg-google"
    assert result.mime_type == "audio/ogg"


async def test_azure_voice_and_synthesis_contracts():
    provider = AzureSpeechProvider("azure-secret", "eastus")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["ocp-apim-subscription-key"] == "azure-secret"
        if request.method == "GET":
            assert request.url == httpx.URL(
                "https://eastus.tts.speech.microsoft.com/cognitiveservices/voices/list"
            )
            return httpx.Response(
                200,
                json=[
                    {
                        "ShortName": "fr-FR-DeniseNeural",
                        "DisplayName": "Denise",
                        "Locale": "fr-FR",
                        "Gender": "Female",
                        "VoiceType": "Neural",
                        "StyleList": ["cheerful"],
                        "WordsPerMinute": "165",
                    },
                    {"ShortName": "en-US-JennyNeural", "Locale": "en-US"},
                ],
            )

        assert request.url == httpx.URL(
            "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
        )
        assert request.headers["content-type"] == "application/ssml+xml"
        assert request.headers["x-microsoft-outputformat"] == "riff-24khz-16bit-mono-pcm"
        assert request.content.decode() == (
            "<speak version='1.0' xml:lang='fr-FR'><voice name='Denise&amp;&lt;&gt;'>"
            "<prosody rate='+25%'>Bonjour &lt;&amp;</prosody></voice></speak>"
        )
        return httpx.Response(
            200,
            content=b"RIFF-azure",
            headers={"x-requestid": "azure-request"},
        )

    await install_transport(provider, handler)
    try:
        voices = await provider.list_voices(language="fr", limit=2)
        result = await provider.generate(
            SpeechRequest(
                text="Bonjour <&",
                voice_id="Denise&<>",
                output_format="wav",
                language="fr-FR",
                speed=1.25,
            )
        )
    finally:
        await provider.aclose()

    assert [(voice.id, voice.name, voice.language) for voice in voices] == [
        ("fr-FR-DeniseNeural", "Denise", "fr-FR")
    ]
    assert voices[0].metadata == {
        "styles": ["cheerful"],
        "words_per_minute": "165",
    }
    assert result.audio == b"RIFF-azure"
    assert result.mime_type == "audio/wav"
    assert result.request_id == "azure-request"


@pytest.mark.parametrize(
    ("factory", "speech_request", "message"),
    [
        (
            lambda: ElevenLabsProvider("key"),
            SpeechRequest(text="Hello"),
            "ElevenLabs requires voice_id",
        ),
        (
            lambda: ElevenLabsProvider("key"),
            SpeechRequest(text="Hello", voice_id="../unsafe"),
            "ElevenLabs voice_id is invalid",
        ),
        (
            lambda: HumeProvider("key"),
            SpeechRequest(text="Hello"),
            "Hume Octave 2 requires voice_id",
        ),
        (
            lambda: HumeProvider("key"),
            SpeechRequest(text="Hello", voice_id="voice-1", model="octave-3"),
            "Hume model must be 'octave-1' or 'octave-2'",
        ),
        (
            lambda: HumeProvider("key"),
            SpeechRequest(
                text="Hello",
                voice_id="voice-1",
                options={"num_generations": "many"},
            ),
            "Hume num_generations must be 1",
        ),
        (
            lambda: CartesiaProvider("key", "2026-08-14"),
            SpeechRequest(text="Hello"),
            "Cartesia requires voice_id",
        ),
        (
            lambda: ResembleProvider("key"),
            SpeechRequest(text="Hello"),
            "Resemble requires voice_id",
        ),
        (
            lambda: OpenAIVoiceProvider("key"),
            SpeechRequest(text="Hello"),
            "OpenAI requires voice_id",
        ),
        (
            lambda: OpenAIVoiceProvider("key"),
            SpeechRequest(
                text="Hello",
                voice_id="alloy",
                model="tts-1",
                instructions="Warm",
            ),
            "OpenAI model 'tts-1' does not support instructions",
        ),
        (
            lambda: GoogleCloudProvider("project"),
            SpeechRequest(text="Hello"),
            "Google Cloud TTS requires voice_id",
        ),
        (
            lambda: AzureSpeechProvider("key", "eastus"),
            SpeechRequest(text="Hello"),
            "Azure Speech requires voice_id",
        ),
    ],
)
async def test_provider_specific_validation_happens_before_http(factory, speech_request, message):
    provider = factory()
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate(speech_request)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"use_hd": "false"}, "Resemble use_hd must be a boolean"),
        ({"sample_rate": "fast"}, "Resemble sample_rate must be a positive integer"),
        ({"sample_rate": -1}, "Resemble sample_rate must be a positive integer"),
    ],
)
async def test_resemble_rejects_invalid_option_values_before_http(options, message):
    provider = ResembleProvider("key")
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate(
                SpeechRequest(text="Hello", voice_id="voice-1", options=options)
            )
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("provider", "low", "high"),
    [
        (ElevenLabsProvider("key"), 0.7, 1.2),
        (HumeProvider("key"), 0.5, 2.0),
        (CartesiaProvider("key", "2026-08-14"), 0.6, 1.5),
        (GoogleCloudProvider("project", enabled=True), 0.25, 2.0),
        (AzureSpeechProvider("key", "eastus"), 0.5, 2.0),
    ],
)
async def test_provider_specific_speed_ranges(provider, low, high):
    try:
        provider.validate_request(SpeechRequest(text="Hello", speed=low))
        provider.validate_request(SpeechRequest(text="Hello", speed=high))
        with pytest.raises(ProviderError, match="speed must be between"):
            provider.validate_request(SpeechRequest(text="Hello", speed=low - 0.01))
        with pytest.raises(ProviderError, match="speed must be between"):
            provider.validate_request(SpeechRequest(text="Hello", speed=high + 0.01))
    finally:
        await provider.aclose()


async def test_hume_octave_2_rejects_delivery_instructions_before_http():
    provider = HumeProvider("key")
    try:
        with pytest.raises(ProviderError, match="Octave 2 does not currently support"):
            await provider.generate(
                SpeechRequest(
                    text="Hello",
                    voice_id="voice-1",
                    model="octave-2",
                    instructions="Warm",
                )
            )
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"stability": -0.01}, "ElevenLabs stability must be a number between 0 and 1"),
        (
            {"similarity_boost": 1.01},
            "ElevenLabs similarity_boost must be a number between 0 and 1",
        ),
        ({"style": "dramatic"}, "ElevenLabs style must be a number between 0 and 1"),
        ({"use_speaker_boost": 1}, "ElevenLabs use_speaker_boost must be a boolean"),
    ],
)
async def test_elevenlabs_rejects_invalid_option_values_before_http(options, message):
    provider = ElevenLabsProvider("key")
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate(
                SpeechRequest(text="Hello", voice_id="voice-1", options=options)
            )
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"volume": 0.49}, "Cartesia volume must be a number between 0.5 and 2"),
        ({"volume": float("nan")}, "Cartesia volume must be a number between 0.5 and 2"),
        ({"emotion": "joyful"}, "Cartesia emotion is not supported"),
        ({"emotion": 1}, "Cartesia emotion is not supported"),
    ],
)
async def test_cartesia_rejects_invalid_option_values_before_http(options, message):
    provider = CartesiaProvider("key", "2026-08-14")
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate(
                SpeechRequest(text="Hello", voice_id="voice-1", options=options)
            )
    finally:
        await provider.aclose()


async def test_cartesia_rejects_emotion_for_non_english_language_before_http():
    provider = CartesiaProvider("key", "2026-08-14")
    try:
        with pytest.raises(ProviderError, match="emotion is supported only for English"):
            await provider.generate(
                SpeechRequest(
                    text="Hola",
                    voice_id="voice-1",
                    language="es",
                    options={"emotion": "calm"},
                )
            )
    finally:
        await provider.aclose()


async def test_google_configuration_requires_credentials_or_explicit_enable(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    provider = GoogleCloudProvider("project")
    enabled = GoogleCloudProvider("project", enabled=True)
    file_configured = GoogleCloudProvider(
        "project", credentials_file="credentials-from-dotenv.json"
    )
    try:
        assert provider.configured is False
        assert enabled.configured is True
        assert file_configured.configured is True
    finally:
        await provider.aclose()
        await enabled.aclose()
        await file_configured.aclose()


async def test_google_file_credentials_apply_auth_and_quota_headers(monkeypatch):
    captured = {}

    class FakeCredentials:
        def before_request(self, request, method, url, headers):
            captured["before_request"] = (request, method, url)
            headers["authorization"] = "Bearer applied-token"
            headers["x-goog-user-project"] = "quota-project"

    def load_credentials(filename, *, scopes, quota_project_id):
        captured["load"] = (filename, scopes, quota_project_id)
        return FakeCredentials(), "detected-project"

    monkeypatch.setattr(
        "voxbridge.providers.google_cloud.google.auth.load_credentials_from_file",
        load_credentials,
    )
    provider = GoogleCloudProvider(
        "quota-project",
        credentials_file="credentials.json",
    )
    try:
        headers = provider._authorization_headers(
            "GET", "https://texttospeech.googleapis.com/v1/voices"
        )
    finally:
        await provider.aclose()

    assert captured["load"] == (
        "credentials.json",
        ["https://www.googleapis.com/auth/cloud-platform"],
        "quota-project",
    )
    assert captured["before_request"][1:] == (
        "GET",
        "https://texttospeech.googleapis.com/v1/voices",
    )
    assert headers == {
        "authorization": "Bearer applied-token",
        "x-goog-user-project": "quota-project",
    }


async def test_google_detected_project_is_not_promoted_to_quota_project(monkeypatch):
    quota_project_arguments = []
    before_request_calls = []

    class FakeCredentials:
        def before_request(self, request, method, url, headers):
            before_request_calls.append((method, url))
            headers["authorization"] = "Bearer applied-token"

    def default_credentials(*, scopes, quota_project_id):
        quota_project_arguments.append(quota_project_id)
        return FakeCredentials(), "detected-resource-project"

    monkeypatch.setattr(
        "voxbridge.providers.google_cloud.google.auth.default",
        default_credentials,
    )
    provider = GoogleCloudProvider(None, enabled=True)
    try:
        provider._authorization_headers("GET", "https://texttospeech.googleapis.com/v1/voices")
        provider._authorization_headers(
            "POST", "https://texttospeech.googleapis.com/v1/text:synthesize"
        )
    finally:
        await provider.aclose()

    assert quota_project_arguments == [None]
    assert before_request_calls == [
        ("GET", "https://texttospeech.googleapis.com/v1/voices"),
        ("POST", "https://texttospeech.googleapis.com/v1/text:synthesize"),
    ]


async def test_google_lyria_configuration_requires_explicit_enable_and_project():
    disabled = GoogleLyriaProvider("project", enabled=False)
    missing_project = GoogleLyriaProvider(None, enabled=True)
    configured = GoogleLyriaProvider("project", enabled=True)
    try:
        assert disabled.configured is False
        assert missing_project.configured is False
        assert configured.configured is True
    finally:
        await disabled.aclose()
        await missing_project.aclose()
        await configured.aclose()


def test_google_lyria_rejects_non_global_location_before_authentication():
    with pytest.raises(ValueError, match="location must be global"):
        GoogleLyriaProvider(
            "project",
            enabled=True,
            location="attacker.example",
        )


async def test_google_lyria_music_contract_and_result_metadata(monkeypatch):
    provider = GoogleLyriaProvider(
        "project-123",
        enabled=True,
        location="global",
        music_timeout=91.0,
    )
    auth = AsyncMock(return_value={"Authorization": "Bearer google-token"})
    monkeypatch.setattr(provider, "_auth_headers", auth)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "https://aiplatform.googleapis.com/v1/projects/project-123/locations/global/"
            "publishers/google/models/lyria-002:predict"
        )
        assert request.headers["authorization"] == "Bearer google-token"
        assert body(request) == {
            "instances": [
                {
                    "prompt": "A warm cinematic instrumental with strings",
                    "negative_prompt": "vocals, harsh percussion",
                    "seed": 98765,
                }
            ],
            "parameters": {},
        }
        assert request.extensions["timeout"] == {
            "connect": 91.0,
            "read": 91.0,
            "write": 91.0,
            "pool": 91.0,
        }
        return httpx.Response(
            200,
            json={
                "predictions": [
                    {
                        "audioContent": base64.b64encode(b"RIFF-google-lyria").decode(),
                        "mimeType": "audio/wav",
                    }
                ],
                "modelDisplayName": "Lyria 2",
            },
            headers={"x-goog-request-id": "lyria-request-1"},
        )

    await install_transport(provider, handler)
    try:
        result = await provider.generate_music(
            MusicRequest(
                prompt="A warm cinematic instrumental with strings",
                negative_prompt="vocals, harsh percussion",
                model="lyria-002",
                output_format="wav",
                seed=98765,
            )
        )
    finally:
        await provider.aclose()

    auth.assert_awaited_once()
    assert result.audio == b"RIFF-google-lyria"
    assert result.mime_type == "audio/wav"
    assert result.provider == "google-lyria"
    assert result.model == "lyria-002"
    assert result.request_id == "lyria-request-1"
    assert result.metadata == {
        "music": True,
        "music_mode": "prompt",
        "duration_control": "provider_fixed",
        "force_instrumental": True,
        "seed": 98765,
        "model_display_name": "Lyria 2",
    }


@pytest.mark.parametrize(
    ("music_request", "message"),
    [
        (MusicRequest(model="lyria-002", output_format="wav"), "requires a non-empty"),
        (
            MusicRequest(
                prompt="Music",
                composition_plan={"sections": []},
                model="lyria-002",
                output_format="wav",
            ),
            "does not support composition plans",
        ),
        (
            MusicRequest(prompt="Music", model="music_v2_5", output_format="wav"),
            "music model must be lyria-002",
        ),
        (
            MusicRequest(prompt="Music", model="lyria-002", output_format="mp3"),
            "output must be wav",
        ),
        (
            MusicRequest(
                prompt="Music",
                music_length_ms=30_000,
                model="lyria-002",
                output_format="wav",
            ),
            "provider-fixed clip duration",
        ),
        (
            MusicRequest(
                prompt="Music",
                negative_prompt="   ",
                model="lyria-002",
                output_format="wav",
            ),
            "negative_prompt cannot be blank",
        ),
        (
            MusicRequest(
                prompt="Music",
                finetune_id="custom",
                model="lyria-002",
                output_format="wav",
            ),
            "does not support finetune_id",
        ),
        (
            MusicRequest(
                prompt="Music",
                sign_with_c2pa=True,
                model="lyria-002",
                output_format="wav",
            ),
            "does not expose C2PA signing",
        ),
    ],
)
async def test_google_lyria_rejects_invalid_requests_before_http(music_request, message):
    provider = GoogleLyriaProvider("project", enabled=True)
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate_music(music_request)
    finally:
        await provider.aclose()


async def test_stability_music_contract_and_result_metadata():
    provider = StabilityAudioProvider("stability-secret", generation_timeout=82.0)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL(
            "https://api.stability.ai/v2beta/audio/stable-audio-2/text-to-audio"
        )
        assert request.headers["authorization"] == "Bearer stability-secret"
        assert request.headers["accept"] == "audio/*"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        multipart = request.content
        for name, value in (
            (b"prompt", b"A bright instrumental logo sting"),
            (b"duration", b"4.0"),
            (b"model", b"stable-audio-2.5"),
            (b"output_format", b"wav"),
            (b"seed", b"4294967294"),
        ):
            assert b'name="' + name + b'"' in multipart
            assert value in multipart
        assert request.extensions["timeout"] == {
            "connect": 82.0,
            "read": 82.0,
            "write": 82.0,
            "pool": 82.0,
        }
        return httpx.Response(
            200,
            content=b"RIFF-stability-music",
            headers={"content-type": "audio/wav", "x-request-id": "stable-request-1"},
        )

    await install_transport(provider, handler)
    try:
        result = await provider.generate_music(
            MusicRequest(
                prompt="A bright instrumental logo sting",
                music_length_ms=4_000,
                model="stable-audio-2.5",
                output_format="wav",
                seed=4_294_967_294,
            )
        )
    finally:
        await provider.aclose()

    assert result.audio == b"RIFF-stability-music"
    assert result.mime_type == "audio/wav"
    assert result.provider == "stability"
    assert result.model == "stable-audio-2.5"
    assert result.request_id == "stable-request-1"
    assert result.metadata == {
        "duration_seconds": 4.0,
        "seed": 4_294_967_294,
        "credits_per_successful_generation": 20,
        "music": True,
        "music_mode": "prompt",
        "music_length_ms": 4_000,
    }


async def test_stability_sound_effect_contract_and_result_metadata():
    provider = StabilityAudioProvider("stability-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        multipart = request.content
        assert b"A sharp sci-fi door hiss" in multipart
        assert b'name="duration"' in multipart
        assert b"5.0" in multipart
        assert b'name="output_format"' in multipart
        assert b"mp3" in multipart
        return httpx.Response(200, content=b"ID3-stability-effect")

    await install_transport(provider, handler)
    try:
        result = await provider.generate_sound_effect(
            SoundEffectRequest(
                prompt="A sharp sci-fi door hiss",
                duration_seconds=5.0,
                model="stable-audio-2.5",
                output_format="mp3",
            )
        )
    finally:
        await provider.aclose()

    assert result.mime_type == "audio/mpeg"
    assert result.metadata == {
        "duration_seconds": 5.0,
        "seed": None,
        "credits_per_successful_generation": 20,
        "sound_effect": True,
    }


async def test_stability_rejects_non_audio_success_response():
    provider = StabilityAudioProvider("stability-secret")

    await install_transport(
        provider,
        lambda request: httpx.Response(
            200,
            json={"error": "unexpected success envelope"},
            headers={"content-type": "application/json"},
        ),
    )
    try:
        with pytest.raises(ProviderError, match="invalid audio response"):
            await provider.generate_sound_effect(
                SoundEffectRequest(
                    prompt="A short impact",
                    duration_seconds=1.0,
                    model="stable-audio-2.5",
                    output_format="mp3",
                )
            )
    finally:
        await provider.aclose()


async def test_stability_rejects_wav_that_would_exceed_gateway_limit_before_http():
    provider = StabilityAudioProvider("stability-secret")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"unexpected")

    await install_transport(provider, handler)
    try:
        assert 59 < provider.wav_duration_max_seconds_for_audio_limit < 60
        with pytest.raises(ProviderError, match="WAV duration exceeds"):
            await provider.generate_music(
                MusicRequest(
                    prompt="A long ambient bed",
                    music_length_ms=60_000,
                    model="stable-audio-2.5",
                    output_format="wav",
                )
            )
    finally:
        await provider.aclose()

    assert calls == 0


@pytest.mark.parametrize(
    ("request_kind", "candidate", "message"),
    [
        (
            "music",
            MusicRequest(
                prompt="Music",
                composition_plan={"sections": []},
                model="stable-audio-2.5",
            ),
            "does not support composition plans",
        ),
        (
            "music",
            MusicRequest(prompt="Music", model="stable-audio-2.5", music_length_ms=999),
            "duration must be between 1 and 190",
        ),
        (
            "music",
            MusicRequest(prompt="Music", model="stable-audio-2.5", seed=4_294_967_295),
            "seed must be between 0 and 4294967294",
        ),
        (
            "music",
            MusicRequest(
                prompt="Music",
                model="stable-audio-2.5",
                negative_prompt="vocals",
            ),
            "does not expose negative_prompt",
        ),
        (
            "sound_effect",
            SoundEffectRequest(
                prompt="Effect",
                duration_seconds=191,
                model="stable-audio-2.5",
            ),
            "duration must be between 1 and 190",
        ),
        (
            "sound_effect",
            SoundEffectRequest(prompt="Effect", loop=True, model="stable-audio-2.5"),
            "does not expose seamless looping",
        ),
        (
            "sound_effect",
            SoundEffectRequest(
                prompt="Effect",
                prompt_influence=0.3,
                model="stable-audio-2.5",
            ),
            "does not expose prompt_influence",
        ),
    ],
)
async def test_stability_rejects_invalid_requests_before_http(request_kind, candidate, message):
    provider = StabilityAudioProvider("key")
    try:
        with pytest.raises(ProviderError, match=message):
            if request_kind == "music":
                await provider.generate_music(candidate)
            else:
                await provider.generate_sound_effect(candidate)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("speech_request", "message"),
    [
        (
            SpeechRequest(text="Hello", voice_id="voice-1", language="en"),
            "language is not supported by model 'eleven_multilingual_v2'",
        ),
        (
            SpeechRequest(text="Hello", voice_id="voice-1", model="eleven_v3", speed=1.1),
            "model 'eleven_v3' does not support speed",
        ),
        (
            SpeechRequest(
                text="Hello",
                voice_id="voice-1",
                model="eleven_v3",
                options={"similarity_boost": 0.8},
            ),
            r"model 'eleven_v3' does not support option\(s\): similarity_boost",
        ),
        (
            SpeechRequest(
                text="Hello",
                voice_id="voice-1",
                model="eleven_v3",
                options={"use_speaker_boost": True},
            ),
            r"model 'eleven_v3' does not support option\(s\): use_speaker_boost",
        ),
    ],
)
async def test_elevenlabs_rejects_model_specific_unsupported_controls_before_http(
    speech_request, message
):
    provider = ElevenLabsProvider("key")
    try:
        with pytest.raises(ProviderError, match=message):
            await provider.generate(speech_request)
    finally:
        await provider.aclose()


async def test_azure_rejects_an_unsafe_region_before_http():
    provider = AzureSpeechProvider("key", "eastus.example.invalid/path")
    try:
        with pytest.raises(ProviderError, match="Azure Speech region is invalid"):
            await provider.generate(SpeechRequest(text="Hello", voice_id="voice-1"))
    finally:
        await provider.aclose()
