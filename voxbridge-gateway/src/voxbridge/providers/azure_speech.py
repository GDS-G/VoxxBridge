from __future__ import annotations

import re
from html import escape

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider


class AzureSpeechProvider(VoiceProvider):
    id = "azure"
    display_name = "Microsoft Azure Speech"
    capabilities = ("text_to_speech", "list_voices")
    supported_formats = ("mp3", "wav")
    max_text_chars = 3_000
    supports_language = True
    supports_speed = True
    min_speed = 0.5
    max_speed = 2.0

    def __init__(self, api_key: str | None, region: str | None, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key
        self.region = region

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.region)

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise ProviderError("Azure Speech requires AZURE_SPEECH_KEY and AZURE_SPEECH_REGION")
        return {"Ocp-Apim-Subscription-Key": self.api_key or ""}

    def _base_url(self) -> str:
        region = (self.region or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9-]+", region):
            raise ProviderError("Azure Speech region is invalid")
        return f"https://{region}.tts.speech.microsoft.com"

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        url = f"{self._base_url()}/cognitiveservices/voices/list"
        r = await self._request("GET", url, headers=self._headers())
        items = self.parse_json(r)
        if not isinstance(items, list):
            raise ProviderError("Azure Speech returned an invalid voice list")
        if language:
            items = [
                v for v in items if str(v.get("Locale", "")).lower().startswith(language.lower())
            ]
        return [
            Voice(
                id=v.get("ShortName") or v.get("Name", ""),
                name=v.get("DisplayName") or v.get("ShortName") or v.get("Name", ""),
                provider=self.id,
                language=v.get("Locale"),
                gender=v.get("Gender"),
                description=v.get("VoiceType"),
                metadata={
                    "styles": v.get("StyleList"),
                    "words_per_minute": v.get("WordsPerMinute"),
                },
            )
            for v in items[:limit]
        ]

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        if not req.voice_id:
            raise ProviderError("Azure Speech requires voice_id")
        lang = req.language or "en-US"
        rate = "0%" if req.speed is None else f"{(req.speed - 1.0) * 100:+.0f}%"
        text = escape(req.text)
        ssml = f"<speak version='1.0' xml:lang='{escape(lang)}'><voice name='{escape(req.voice_id)}'><prosody rate='{rate}'>{text}</prosody></voice></speak>"
        fmt = req.output_format.lower()
        out_fmt = (
            "audio-24khz-160kbitrate-mono-mp3" if fmt == "mp3" else "riff-24khz-16bit-mono-pcm"
        )
        url = f"{self._base_url()}/cognitiveservices/v1"
        headers = {
            **self._headers(),
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": out_fmt,
            "User-Agent": "VoxBridge",
        }
        r = await self._request("POST", url, headers=headers, content=ssml.encode("utf-8"))
        self.validate_audio(r.content)
        return SpeechResult(
            r.content,
            "audio/mpeg" if fmt == "mp3" else "audio/wav",
            self.id,
            request_id=r.headers.get("x-requestid"),
        )
