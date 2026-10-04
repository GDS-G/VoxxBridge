from __future__ import annotations

import base64
import binascii
from typing import ClassVar

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider


class HumeProvider(VoiceProvider):
    id = "hume"
    display_name = "Hume AI"
    default_model = "octave-2"
    capabilities = ("text_to_speech", "list_voices", "acting_instructions")
    supported_formats = ("mp3", "wav", "pcm")
    allowed_options = frozenset({"num_generations"})
    max_text_chars = 5_000
    supports_instructions = True
    instructions_note = "Delivery instructions currently require model 'octave-1'."
    control_notes: ClassVar[dict[str, str]] = {
        "service_sunset": (
            "Hume states that its TTS and EVI APIs will shut down on November 13, 2026."
        )
    }
    supports_model = True
    supports_speed = True
    min_speed = 0.5
    max_speed = 2.0
    base64_audio_response = True

    def __init__(self, api_key: str | None, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("Hume is not configured")
        return {"X-Hume-Api-Key": self.api_key}

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        voices: list[Voice] = []
        for source in ("HUME_AI", "CUSTOM_VOICE"):
            params: dict[str, str | int | bool] = {
                "provider": source,
                "page_size": min(max(limit, 1), 100),
            }
            if language:
                params["filter_tag"] = f"LANGUAGE:{language}"
            r = await self._request(
                "GET", "https://api.hume.ai/v0/tts/voices", headers=self._headers(), params=params
            )
            data = self.parse_json(r)
            if not isinstance(data, dict):
                raise ProviderError("Hume returned an invalid voice list")
            for v in data.get("voices_page", []):
                voices.append(
                    Voice(
                        v.get("id", ""),
                        v.get("name", ""),
                        self.id,
                        metadata={"voice_provider": v.get("provider", source)},
                    )
                )
                if len(voices) >= limit:
                    return voices
        return voices

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        model = req.model or self.default_model
        if model not in {"octave-1", "octave-2"}:
            raise ProviderError("Hume model must be 'octave-1' or 'octave-2'")
        if model == "octave-2" and not req.voice_id:
            raise ProviderError("Hume Octave 2 requires voice_id")
        if model == "octave-2" and req.instructions:
            raise ProviderError(
                "Hume Octave 2 does not currently support delivery instructions; "
                "use model 'octave-1'"
            )
        if req.instructions and len(req.instructions) > 1_000:
            raise ProviderError("Hume delivery instructions accept at most 1,000 characters")
        try:
            num_generations = int(req.options.get("num_generations", 1))
        except (TypeError, ValueError) as exc:
            raise ProviderError("Hume num_generations must be 1") from exc
        if num_generations != 1:
            raise ProviderError(
                "VoxBridge currently returns one Hume generation; num_generations must be 1"
            )
        utterance: dict = {"text": req.text}
        if req.voice_id:
            utterance["voice"] = {"id": req.voice_id}
        if req.instructions:
            utterance["description"] = req.instructions
        if req.speed is not None:
            utterance["speed"] = req.speed
        payload: dict = {
            "utterances": [utterance],
            "format": {
                "type": req.output_format.lower()
                if req.output_format.lower() in {"mp3", "wav", "pcm"}
                else "mp3"
            },
            "num_generations": 1,
            "version": "2" if model == "octave-2" else "1",
        }
        r = await self._request(
            "POST",
            "https://api.hume.ai/v0/tts",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
        )
        data = self.parse_json(r)
        if not isinstance(data, dict):
            raise ProviderError("Hume returned an invalid synthesis response")
        generations = data.get("generations") or []
        if not generations:
            raise ProviderError("Hume returned no generations")
        gen = generations[0]
        try:
            audio = base64.b64decode(gen.get("audio", ""), validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProviderError("Hume returned malformed audio") from exc
        self.validate_audio(audio)
        encoding = (gen.get("encoding") or {}).get("format", req.output_format.lower())
        mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "pcm": "audio/L16"}.get(
            encoding, f"audio/{encoding}"
        )
        return SpeechResult(
            audio,
            mime,
            self.id,
            model,
            data.get("request_id"),
            {"generation_id": gen.get("generation_id"), "duration": gen.get("duration")},
        )
