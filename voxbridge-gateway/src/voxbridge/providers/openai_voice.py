from __future__ import annotations

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider

_OPENAI_VOICES = [
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
]


class OpenAIVoiceProvider(VoiceProvider):
    id = "openai"
    display_name = "OpenAI"
    default_model = "gpt-4o-mini-tts"
    capabilities = ("text_to_speech", "list_voices", "voice_instructions")
    supported_formats = ("mp3", "opus", "aac", "flac", "wav", "pcm")
    max_text_chars = 4_096
    supports_instructions = True
    instructions_note = "Instructions are not supported by the tts-1 or tts-1-hd models."
    supports_model = True
    supports_speed = True

    def __init__(self, api_key: str | None, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        return [
            Voice(v, v.title(), self.id, language="multilingual") for v in _OPENAI_VOICES[:limit]
        ]

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        if not self.api_key:
            raise ProviderError("OpenAI is not configured")
        if not req.voice_id:
            raise ProviderError("OpenAI requires voice_id")
        fmt = (
            req.output_format.lower()
            if req.output_format.lower() in {"mp3", "opus", "aac", "flac", "wav", "pcm"}
            else "mp3"
        )
        model = req.model or self.default_model
        if req.instructions and model in {"tts-1", "tts-1-hd"}:
            raise ProviderError(f"OpenAI model '{model}' does not support instructions")
        voice: str | dict[str, str]
        voice = {"id": req.voice_id} if req.voice_id.startswith("voice_") else req.voice_id
        payload: dict = {
            "model": model,
            "input": req.text,
            "voice": voice,
            "response_format": fmt,
        }
        if req.instructions:
            payload["instructions"] = req.instructions
        if req.speed is not None:
            payload["speed"] = req.speed
        r = await self._request(
            "POST",
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        self.validate_audio(r.content)
        mime = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "opus": "audio/ogg",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "pcm": "audio/L16",
        }[fmt]
        return SpeechResult(r.content, mime, self.id, model, r.headers.get("x-request-id"))
