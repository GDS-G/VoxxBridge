from __future__ import annotations

from typing import ClassVar

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider


class ElevenLabsProvider(VoiceProvider):
    id = "elevenlabs"
    display_name = "ElevenLabs"
    default_model = "eleven_multilingual_v2"
    capabilities = ("text_to_speech", "list_voices")
    supported_formats = ("mp3", "wav")
    allowed_options = frozenset({"stability", "similarity_boost", "style", "use_speaker_boost"})
    max_text_chars = 5_000
    supports_language = True
    supports_model = True
    supports_speed = True
    min_speed = 0.7
    max_speed = 1.2
    control_notes: ClassVar[dict[str, str]] = {
        "language": "language is not supported by eleven_multilingual_v2",
        "eleven_v3": "speed, similarity_boost, and use_speaker_boost are unavailable",
    }

    def __init__(self, api_key: str | None, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("ElevenLabs is not configured")
        return {"xi-api-key": self.api_key}

    def validate_request(self, req: SpeechRequest) -> None:
        super().validate_request(req)
        model = req.model or self.default_model
        if req.language and model == "eleven_multilingual_v2":
            raise ProviderError(
                "ElevenLabs language is not supported by model 'eleven_multilingual_v2'"
            )
        if model == "eleven_v3":
            if req.speed is not None:
                raise ProviderError("ElevenLabs model 'eleven_v3' does not support speed")
            unavailable = sorted(set(req.options) & {"similarity_boost", "use_speaker_boost"})
            if unavailable:
                raise ProviderError(
                    "ElevenLabs model 'eleven_v3' does not support option(s): "
                    + ", ".join(unavailable)
                )

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        params: dict[str, str | int] = {"page_size": min(max(limit, 1), 100)}
        if language:
            params["language"] = language
        r = await self._request(
            "GET", "https://api.elevenlabs.io/v2/voices", headers=self._headers(), params=params
        )
        out: list[Voice] = []
        data = self.parse_json(r)
        if not isinstance(data, dict):
            raise ProviderError("ElevenLabs returned an invalid voice list")
        for v in data.get("voices", [])[:limit]:
            labels = v.get("labels") or {}
            out.append(
                Voice(
                    id=v.get("voice_id", ""),
                    name=v.get("name") or v.get("voice_id", ""),
                    provider=self.id,
                    language=(v.get("verified_languages") or [{}])[0].get("language")
                    if v.get("verified_languages")
                    else None,
                    gender=labels.get("gender"),
                    description=v.get("description"),
                    preview_url=v.get("preview_url"),
                    metadata={"category": v.get("category"), "labels": labels},
                )
            )
        return out

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        if not req.voice_id:
            raise ProviderError("ElevenLabs requires voice_id")
        if any(char in req.voice_id for char in "/?#"):
            raise ProviderError("ElevenLabs voice_id is invalid")
        fmt_map = {
            "mp3": "mp3_44100_128",
            "wav": "wav_24000",
        }
        output = fmt_map[req.output_format.lower()]
        payload: dict = {"text": req.text, "model_id": req.model or self.default_model}
        if req.language:
            payload["language_code"] = req.language
        voice_settings = {}
        if req.speed is not None:
            voice_settings["speed"] = req.speed
        for key in ("stability", "similarity_boost", "style"):
            if key not in req.options:
                continue
            value = req.options[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                raise ProviderError(f"ElevenLabs {key} must be a number between 0 and 1")
            voice_settings[key] = value
        if "use_speaker_boost" in req.options:
            if not isinstance(req.options["use_speaker_boost"], bool):
                raise ProviderError("ElevenLabs use_speaker_boost must be a boolean")
            voice_settings["use_speaker_boost"] = req.options["use_speaker_boost"]
        if voice_settings:
            payload["voice_settings"] = voice_settings
        r = await self._request(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{req.voice_id}",
            headers={**self._headers(), "Content-Type": "application/json"},
            params={"output_format": output},
            json=payload,
        )
        default_mime = "audio/wav" if req.output_format == "wav" else "audio/mpeg"
        mime = r.headers.get("content-type", default_mime).split(";")[0]
        self.validate_audio(r.content)
        return SpeechResult(
            r.content,
            mime,
            self.id,
            req.model or self.default_model,
            r.headers.get("request-id"),
            {"character_cost": r.headers.get("character-cost")},
        )
