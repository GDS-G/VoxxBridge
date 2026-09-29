from __future__ import annotations

import base64
import binascii

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider


class ResembleProvider(VoiceProvider):
    id = "resemble"
    display_name = "Resemble AI"
    capabilities = ("text_to_speech", "list_voices")
    supported_formats = ("mp3", "wav")
    allowed_options = frozenset({"use_hd", "sample_rate"})
    max_text_chars = 3_000
    base64_audio_response = True

    def __init__(self, api_key: str | None, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("Resemble is not configured")
        return {"Authorization": f"Bearer {self.api_key}"}

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        params: dict[str, str | int | bool] = {
            "page": 1,
            "page_size": min(max(limit, 10), 1000),
            "sample_url": True,
        }
        r = await self._request(
            "GET", "https://app.resemble.ai/api/v2/voices", headers=self._headers(), params=params
        )
        data = self.parse_json(r)
        if not isinstance(data, dict):
            raise ProviderError("Resemble returned an invalid voice list")
        items = data.get("items") or []
        out = []
        for v in items:
            lang = v.get("language") or v.get("default_language")
            if language and lang and not str(lang).lower().startswith(language.lower()):
                continue
            out.append(
                Voice(
                    id=v.get("uuid") or v.get("voice_uuid") or v.get("id", ""),
                    name=v.get("name") or v.get("title") or "Resemble voice",
                    provider=self.id,
                    language=lang,
                    gender=v.get("gender"),
                    description=v.get("description"),
                    preview_url=v.get("sample_url"),
                    metadata={k: v.get(k) for k in ("status", "accent", "age") if k in v},
                )
            )
            if len(out) >= limit:
                break
        return out

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        if not req.voice_id:
            raise ProviderError("Resemble requires voice_id")
        fmt = req.output_format.lower() if req.output_format.lower() in {"wav", "mp3"} else "mp3"
        payload: dict = {"voice_uuid": req.voice_id, "data": req.text, "output_format": fmt}
        if "use_hd" in req.options:
            if not isinstance(req.options["use_hd"], bool):
                raise ProviderError("Resemble use_hd must be a boolean")
            payload["use_hd"] = req.options["use_hd"]
        if "sample_rate" in req.options:
            raw_sample_rate = req.options["sample_rate"]
            if isinstance(raw_sample_rate, bool):
                raise ProviderError("Resemble sample_rate must be a positive integer")
            try:
                sample_rate = int(raw_sample_rate)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ProviderError("Resemble sample_rate must be a positive integer") from exc
            if sample_rate <= 0:
                raise ProviderError("Resemble sample_rate must be a positive integer")
            payload["sample_rate"] = sample_rate
        r = await self._request(
            "POST",
            "https://f.cluster.resemble.ai/synthesize",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
        )
        data = self.parse_json(r)
        if not isinstance(data, dict):
            raise ProviderError("Resemble returned an invalid synthesis response")
        if data.get("success") is False:
            raise ProviderError("Resemble did not complete the synthesis request")
        try:
            audio = base64.b64decode(data.get("audio_content", ""), validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProviderError("Resemble returned malformed audio") from exc
        self.validate_audio(audio)
        mime = "audio/mpeg" if fmt == "mp3" else "audio/wav"
        return SpeechResult(
            audio,
            mime,
            self.id,
            metadata={
                "duration": data.get("duration"),
                "sample_rate": data.get("sample_rate"),
                "issues": data.get("issues"),
            },
        )
