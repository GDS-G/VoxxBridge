from voxbridge.config import settings
from voxbridge.providers.base import VoiceProvider
from voxbridge.registry import REGISTRY, build_registry, close_registry

EXPECTED_PROVIDERS = {
    "elevenlabs": {
        "capabilities": {"text_to_speech", "list_voices"},
        "formats": {"mp3", "wav"},
        "max_text_chars": 5_000,
        "instructions": False,
        "language": True,
        "model": True,
        "speed": True,
        "speed_range": [0.7, 1.2],
    },
    "hume": {
        "capabilities": {"text_to_speech", "list_voices", "acting_instructions"},
        "formats": {"mp3", "wav", "pcm"},
        "max_text_chars": 5_000,
        "instructions": True,
        "language": False,
        "model": True,
        "speed": True,
        "speed_range": [0.5, 2.0],
    },
    "cartesia": {
        "capabilities": {"text_to_speech", "list_voices"},
        "formats": {"mp3", "wav"},
        "max_text_chars": 3_000,
        "instructions": False,
        "language": True,
        "model": True,
        "speed": True,
        "speed_range": [0.6, 1.5],
    },
    "resemble": {
        "capabilities": {"text_to_speech", "list_voices"},
        "formats": {"mp3", "wav"},
        "max_text_chars": 3_000,
        "instructions": False,
        "language": False,
        "model": False,
        "speed": False,
        "speed_range": None,
    },
    "openai": {
        "capabilities": {"text_to_speech", "list_voices", "voice_instructions"},
        "formats": {"mp3", "opus", "aac", "flac", "wav", "pcm"},
        "max_text_chars": 4_096,
        "instructions": True,
        "language": False,
        "model": True,
        "speed": True,
        "speed_range": [0.25, 4.0],
    },
    "deepgram": {
        "capabilities": {"text_to_speech", "list_voices"},
        "formats": {"mp3", "wav", "opus", "aac", "flac"},
        "max_text_chars": 2_000,
        "instructions": False,
        "language": False,
        "model": True,
        "speed": False,
        "speed_range": None,
    },
    "google": {
        "capabilities": {"text_to_speech", "list_voices"},
        "formats": {"mp3", "wav", "ogg", "opus"},
        "max_text_chars": 3_000,
        "instructions": False,
        "language": True,
        "model": False,
        "speed": True,
        "speed_range": [0.25, 2.0],
    },
    "azure": {
        "capabilities": {"text_to_speech", "list_voices"},
        "formats": {"mp3", "wav"},
        "max_text_chars": 3_000,
        "instructions": False,
        "language": True,
        "model": False,
        "speed": True,
        "speed_range": [0.5, 2.0],
    },
}


def test_all_initial_providers_registered():
    assert set(REGISTRY) == set(EXPECTED_PROVIDERS)
    assert all(key == provider.id for key, provider in REGISTRY.items())
    for provider in REGISTRY.values():
        assert provider.max_audio_bytes == settings.voxbridge_max_audio_bytes
        if provider.base64_audio_response:
            assert provider.max_response_bytes > settings.voxbridge_max_audio_bytes
        else:
            assert provider.max_response_bytes == settings.voxbridge_max_audio_bytes


def test_provider_capabilities_and_limits_are_truthful():
    for provider_id, expected in EXPECTED_PROVIDERS.items():
        provider = REGISTRY[provider_id]
        info = provider.info()

        assert set(provider.capabilities) == expected["capabilities"]
        assert set(provider.supported_formats) == expected["formats"]
        assert provider.max_text_chars == expected["max_text_chars"]
        assert provider.supports_instructions is expected["instructions"]
        assert provider.supports_language is expected["language"]
        assert provider.supports_model is expected["model"]
        assert provider.supports_speed is expected["speed"]

        assert info["id"] == provider_id
        assert set(info["capabilities"]) == expected["capabilities"]
        assert set(info["supported_formats"]) == expected["formats"]
        assert info["max_text_chars"] == expected["max_text_chars"]
        assert info["supports_instructions"] is expected["instructions"]
        assert info["instructions_note"] == provider.instructions_note
        assert info["control_notes"] == provider.control_notes
        assert info["supports_language"] is expected["language"]
        assert info["supports_model"] is expected["model"]
        assert info["supports_speed"] is expected["speed"]
        assert info["speed_range"] == expected["speed_range"]
        assert info["allowed_options"] == sorted(provider.allowed_options)


def test_advertised_list_voices_capability_has_an_implementation():
    for provider in REGISTRY.values():
        advertises_listing = "list_voices" in provider.capabilities
        implements_listing = provider.__class__.list_voices is not VoiceProvider.list_voices
        assert advertises_listing is implements_listing


async def test_registry_instances_are_independent_and_closeable():
    registry = build_registry()
    try:
        assert registry is not REGISTRY
        assert set(registry) == set(EXPECTED_PROVIDERS)
        assert all(registry[key] is not REGISTRY[key] for key in registry)
    finally:
        await close_registry(registry)
    assert all(provider.client.is_closed for provider in registry.values())
