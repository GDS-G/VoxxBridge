from __future__ import annotations

import math
from typing import ClassVar

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider


class CartesiaProvider(VoiceProvider):
    id = "cartesia"
    display_name = "Cartesia"
    default_model = "sonic-3.6"
    capabilities = ("text_to_speech", "list_voices")
    supported_formats = ("mp3", "wav")
    allowed_options = frozenset({"volume", "emotion"})
    max_text_chars = 3_000
    supports_language = True
    supports_model = True
    supports_speed = True
    min_speed = 0.6
    max_speed = 1.5
    control_notes: ClassVar[dict[str, str]] = {
        "emotion": "emotion is supported only for English",
    }

    _emotions = frozenset(
        {
            "neutral",
            "happy",
            "excited",
            "enthusiastic",
            "elated",
            "euphoric",
            "triumphant",
            "amazed",
            "surprised",
            "flirtatious",
            "curious",
            "content",
            "peaceful",
            "serene",
            "calm",
            "grateful",
            "affectionate",
            "trust",
            "sympathetic",
            "anticipation",
            "mysterious",
            "joking/comedic",
            "angry",
            "mad",
            "outraged",
            "frustrated",
            "agitated",
            "threatened",
            "disgusted",
            "contempt",
            "envious",
            "sarcastic",
            "ironic",
            "sad",
            "dejected",
            "melancholic",
            "disappointed",
            "hurt",
            "guilty",
            "bored",
            "tired",
            "rejected",
            "nostalgic",
            "wistful",
            "apologetic",
            "hesitant",
            "insecure",
            "confused",
            "resigned",
            "anxious",
            "panicked",
            "alarmed",
            "scared",
            "proud",
            "confident",
            "distant",
            "skeptical",
            "contemplative",
            "determined",
        }
    )

    def __init__(self, api_key: str | None, version: str, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key
        self.version = version

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("Cartesia is not configured")
        return {"Authorization": f"Bearer {self.api_key}", "Cartesia-Version": self.version}

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        params: dict[str, str | int] = {"limit": min(max(limit, 1), 100)}
        if language:
            params["language"] = language
        r = await self._request(
            "GET", "https://api.cartesia.ai/voices", headers=self._headers(), params=params
        )
        raw = self.parse_json(r)
        if isinstance(raw, dict):
            items = raw.get("data") or raw.get("voices") or []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        return [
            Voice(
                id=v.get("id", ""),
                name=v.get("name") or v.get("id", ""),
                provider=self.id,
                language=(v.get("language") or (v.get("languages") or [None])[0]),
                description=v.get("description"),
                metadata={
                    k: v.get(k) for k in ("is_public", "created_at", "gender", "accent") if k in v
                },
            )
            for v in items[:limit]
        ]

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        if not req.voice_id:
            raise ProviderError("Cartesia requires voice_id")
        fmt = req.output_format.lower()
        if fmt == "mp3":
            output = {"container": "mp3", "bit_rate": 128000, "sample_rate": 44100}
        else:
            output = {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100}
        payload: dict = {
            "model_id": req.model or self.default_model,
            "transcript": req.text,
            "voice": req.voice_id,
            "output_format": output,
        }
        if req.language:
            payload["language"] = req.language
        generation_config = {}
        if req.speed is not None:
            generation_config["speed"] = req.speed
        if "volume" in req.options:
            volume = req.options["volume"]
            if (
                isinstance(volume, bool)
                or not isinstance(volume, (int, float))
                or not math.isfinite(volume)
                or not 0.5 <= volume <= 2.0
            ):
                raise ProviderError("Cartesia volume must be a number between 0.5 and 2")
            generation_config["volume"] = volume
        if "emotion" in req.options:
            emotion = req.options["emotion"]
            if not isinstance(emotion, str) or emotion not in self._emotions:
                raise ProviderError("Cartesia emotion is not supported")
            if req.language:
                language = req.language.lower()
                if language != "en" and not language.startswith("en-"):
                    raise ProviderError("Cartesia emotion is supported only for English")
            generation_config["emotion"] = emotion
        if generation_config:
            payload["generation_config"] = generation_config
        r = await self._request(
            "POST",
            "https://api.cartesia.ai/tts/bytes",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
        )
        self.validate_audio(r.content)
        default_mime = "audio/wav" if fmt == "wav" else "audio/mpeg"
        return SpeechResult(
            r.content,
            r.headers.get("content-type", default_mime).split(";")[0],
            self.id,
            req.model or self.default_model,
            r.headers.get("x-request-id"),
        )
