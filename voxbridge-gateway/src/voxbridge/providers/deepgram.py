from __future__ import annotations

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider

# Representative built-in Aura voice models. VoxBridge also accepts any valid Deepgram model ID.
_DEEPGRAM_VOICES = [
    "aura-2-thalia-en",
    "aura-2-andromeda-en",
    "aura-2-apollo-en",
    "aura-2-arcas-en",
    "aura-2-asteria-en",
    "aura-2-athena-en",
    "aura-2-atlas-en",
    "aura-2-aurora-en",
]


class DeepgramProvider(VoiceProvider):
    id = "deepgram"
    display_name = "Deepgram"
    default_model = "aura-2-thalia-en"
    capabilities = ("text_to_speech", "list_voices")
    supported_formats = ("mp3", "wav", "opus", "aac", "flac")
    max_text_chars = 2_000
    supports_model = True

    def __init__(self, api_key: str | None, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        voices = _DEEPGRAM_VOICES
        if language:
            voices = [v for v in voices if v.endswith(f"-{language}") or f"-{language}-" in v]
        return [Voice(v, v, self.id, language=v.rsplit("-", 1)[-1]) for v in voices[:limit]]

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        if not self.api_key:
            raise ProviderError("Deepgram is not configured")
        model = req.voice_id or req.model or self.default_model
        params: dict[str, str | int] = {"model": model}
        fmt = req.output_format.lower()
        if fmt == "wav":
            params.update({"encoding": "linear16", "container": "wav"})
        else:
            params["encoding"] = fmt
        r = await self._request(
            "POST",
            "https://api.deepgram.com/v1/speak",
            headers={"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"},
            params=params,
            json={"text": req.text},
        )
        self.validate_audio(r.content)
        return SpeechResult(
            r.content,
            r.headers.get("content-type", "audio/mpeg").split(";")[0],
            self.id,
            model,
            r.headers.get("dg-request-id"),
        )
