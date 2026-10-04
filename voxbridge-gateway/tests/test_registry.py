from voxbridge.config import settings
from voxbridge.providers.base import VoiceProvider
from voxbridge.registry import REGISTRY, build_registry, close_registry

EXPECTED_PROVIDERS = {
    "elevenlabs": {
        "capabilities": {
            "text_to_speech",
            "list_voices",
            "music_generation",
            "sound_effect_generation",
        },
        "formats": {"mp3", "wav"},
        "max_text_chars": 5_000,
        "instructions": False,
        "language": True,
        "model": True,
        "speed": True,
        "speed_range": [0.7, 1.2],
        "music_model": "music_v2_5",
        "music_default_length_ms": 30_000,
        "music_models": ["music_v1", "music_v2", "music_v2_5"],
        "music_formats": ["mp3"],
        "music_duration_range_ms": [3_000, 600_000],
        "music_plans": True,
        "sound_effect_model": "eleven_text_to_sound_v2",
        "sound_effect_default_duration_seconds": None,
        "sound_effect_default_prompt_influence": 0.3,
        "sound_effect_models": ["eleven_text_to_sound_v2"],
        "sound_effect_formats": ["mp3"],
        "sound_effect_duration_range_seconds": [0.5, 30.0],
        "max_sound_effect_prompt_chars": 450,
        "sound_effect_loop": True,
        "sound_effect_prompt_influence": True,
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
    "google-lyria": {
        "capabilities": {"music_generation"},
        "formats": set(),
        "max_text_chars": 0,
        "instructions": False,
        "language": False,
        "model": False,
        "speed": False,
        "speed_range": None,
        "music_model": "lyria-002",
        "music_default_length_ms": None,
        "music_models": ["lyria-002"],
        "music_formats": ["wav"],
        "music_duration_range_ms": None,
        "music_plans": False,
    },
    "stability": {
        "capabilities": {"music_generation", "sound_effect_generation"},
        "formats": set(),
        "max_text_chars": 0,
        "instructions": False,
        "language": False,
        "model": False,
        "speed": False,
        "speed_range": None,
        "music_model": "stable-audio-2.5",
        "music_default_length_ms": 30_000,
        "music_models": ["stable-audio-2.5"],
        "music_formats": ["mp3", "wav"],
        "music_duration_range_ms": [1_000, 190_000],
        "music_plans": False,
        "sound_effect_model": "stable-audio-2.5",
        "sound_effect_default_duration_seconds": 5.0,
        "sound_effect_default_prompt_influence": None,
        "sound_effect_models": ["stable-audio-2.5"],
        "sound_effect_formats": ["mp3", "wav"],
        "sound_effect_duration_range_seconds": [1.0, 190.0],
        "max_sound_effect_prompt_chars": 10_000,
        "sound_effect_loop": False,
        "sound_effect_prompt_influence": False,
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
        gateway_formats = expected["formats"] - {"pcm"}
        assert set(info["supported_formats"]) == gateway_formats
        assert set(info["file_delivery_formats"]) == gateway_formats
        assert info["max_audio_bytes"] == settings.voxbridge_max_audio_bytes
        assert info["max_text_chars"] == expected["max_text_chars"]
        assert info["supports_instructions"] is expected["instructions"]
        assert info["instructions_note"] == provider.instructions_note
        assert info["control_notes"] == provider.control_notes
        assert info["supports_language"] is expected["language"]
        assert info["supports_model"] is expected["model"]
        assert info["supports_speed"] is expected["speed"]
        assert info["speed_range"] == expected["speed_range"]
        assert info["allowed_options"] == sorted(provider.allowed_options)
        supports_dialogue = (
            "text_to_speech" in expected["capabilities"] and "wav" in expected["formats"]
        )
        assert info["supports_dialogue"] is supports_dialogue
        assert info["dialogue_output_format"] == ("wav" if supports_dialogue else None)

        if "music_model" in expected:
            assert info["default_music_model"] == expected["music_model"]
            assert info["default_music_length_ms"] == expected["music_default_length_ms"]
            assert info["music_models"] == expected["music_models"]
            assert info["music_supported_formats"] == expected["music_formats"]
            assert info["music_duration_range_ms"] == expected["music_duration_range_ms"]
            assert info["supports_music_composition_plans"] is expected["music_plans"]
        else:
            assert info["default_music_model"] is None
            assert info["default_music_length_ms"] is None
            assert info["music_models"] == []
            assert info["music_supported_formats"] == []
            assert info["music_duration_range_ms"] is None
            assert info["supports_music_composition_plans"] is False
        if "sound_effect_model" in expected:
            assert info["default_sound_effect_model"] == expected["sound_effect_model"]
            assert (
                info["default_sound_effect_duration_seconds"]
                == expected["sound_effect_default_duration_seconds"]
            )
            assert (
                info["default_sound_effect_prompt_influence"]
                == expected["sound_effect_default_prompt_influence"]
            )
            assert info["sound_effect_models"] == expected["sound_effect_models"]
            assert info["sound_effect_supported_formats"] == expected["sound_effect_formats"]
            assert (
                info["sound_effect_duration_range_seconds"]
                == expected["sound_effect_duration_range_seconds"]
            )
            assert (
                info["max_sound_effect_prompt_chars"] == expected["max_sound_effect_prompt_chars"]
            )
            assert info["supports_sound_effect_loop"] is expected["sound_effect_loop"]
            assert (
                info["supports_sound_effect_prompt_influence"]
                is expected["sound_effect_prompt_influence"]
            )
            if provider_id == "stability":
                assert 59 < info["wav_duration_max_seconds_for_audio_limit"] < 60
        else:
            assert info["default_sound_effect_model"] is None
            assert info["default_sound_effect_duration_seconds"] is None
            assert info["default_sound_effect_prompt_influence"] is None
            assert info["sound_effect_models"] == []
            assert info["sound_effect_supported_formats"] == []
            assert info["sound_effect_duration_range_seconds"] is None
            assert info["max_sound_effect_prompt_chars"] is None
            assert info["supports_sound_effect_loop"] is False
            assert info["supports_sound_effect_prompt_influence"] is False


def test_advertised_list_voices_capability_has_an_implementation():
    for provider in REGISTRY.values():
        advertises_listing = "list_voices" in provider.capabilities
        implements_listing = provider.__class__.list_voices is not VoiceProvider.list_voices
        if advertises_listing:
            assert implements_listing


def test_advertised_generation_capabilities_have_implementations():
    for provider in REGISTRY.values():
        if "music_generation" in provider.capabilities:
            assert provider.__class__.generate_music is not VoiceProvider.generate_music
        if "sound_effect_generation" in provider.capabilities:
            assert (
                provider.__class__.generate_sound_effect is not VoiceProvider.generate_sound_effect
            )


async def test_registry_instances_are_independent_and_closeable():
    registry = build_registry()
    try:
        assert registry is not REGISTRY
        assert set(registry) == set(EXPECTED_PROVIDERS)
        assert all(registry[key] is not REGISTRY[key] for key in registry)
    finally:
        await close_registry(registry)
    assert all(provider.client.is_closed for provider in registry.values())
