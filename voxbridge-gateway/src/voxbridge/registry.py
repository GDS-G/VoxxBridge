from __future__ import annotations

import asyncio

from voxbridge.config import settings
from voxbridge.providers.azure_speech import AzureSpeechProvider
from voxbridge.providers.base import VoiceProvider
from voxbridge.providers.cartesia import CartesiaProvider
from voxbridge.providers.deepgram import DeepgramProvider
from voxbridge.providers.elevenlabs import ElevenLabsProvider
from voxbridge.providers.google_cloud import GoogleCloudProvider
from voxbridge.providers.hume import HumeProvider
from voxbridge.providers.openai_voice import OpenAIVoiceProvider
from voxbridge.providers.resemble import ResembleProvider


def build_registry() -> dict[str, VoiceProvider]:
    providers = [
        ElevenLabsProvider(
            settings.elevenlabs_api_key, timeout=settings.voxbridge_request_timeout_seconds
        ),
        HumeProvider(settings.hume_api_key, timeout=settings.voxbridge_request_timeout_seconds),
        CartesiaProvider(
            settings.cartesia_api_key,
            settings.cartesia_version,
            timeout=settings.voxbridge_request_timeout_seconds,
        ),
        ResembleProvider(
            settings.resemble_api_key, timeout=settings.voxbridge_request_timeout_seconds
        ),
        OpenAIVoiceProvider(
            settings.openai_api_key, timeout=settings.voxbridge_request_timeout_seconds
        ),
        DeepgramProvider(
            settings.deepgram_api_key, timeout=settings.voxbridge_request_timeout_seconds
        ),
        GoogleCloudProvider(
            settings.google_cloud_project,
            credentials_file=settings.google_application_credentials,
            enabled=settings.google_cloud_tts_enabled,
            timeout=settings.voxbridge_request_timeout_seconds,
        ),
        AzureSpeechProvider(
            settings.azure_speech_key,
            settings.azure_speech_region,
            timeout=settings.voxbridge_request_timeout_seconds,
        ),
    ]
    for provider in providers:
        provider.configure_audio_limit(settings.voxbridge_max_audio_bytes)
    return {p.id: p for p in providers}


async def close_registry(registry: dict[str, VoiceProvider] | None = None) -> None:
    active = REGISTRY if registry is None else registry
    await asyncio.gather(*(provider.aclose() for provider in active.values()))


REGISTRY = build_registry()
