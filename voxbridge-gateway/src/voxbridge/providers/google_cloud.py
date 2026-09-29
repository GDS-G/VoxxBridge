from __future__ import annotations

import asyncio
import base64
import binascii
import os
import threading
from typing import Any

import google.auth
from google.auth.transport.requests import Request as GoogleAuthRequest

from voxbridge.models import SpeechRequest, SpeechResult, Voice

from .base import ProviderError, VoiceProvider


class GoogleCloudProvider(VoiceProvider):
    id = "google"
    display_name = "Google Cloud Text-to-Speech"
    capabilities = ("text_to_speech", "list_voices")
    supported_formats = ("mp3", "wav", "ogg", "opus")
    max_text_chars = 3_000
    supports_language = True
    supports_speed = True
    min_speed = 0.25
    max_speed = 2.0
    base64_audio_response = True

    def __init__(
        self,
        project: str | None,
        *,
        credentials_file: str | None = None,
        enabled: bool = False,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.quota_project = project
        self.credentials_file = credentials_file or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self.enabled = enabled
        self._credentials: Any | None = None
        self._auth_request = GoogleAuthRequest()
        self._auth_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.enabled or self.credentials_file)

    def _authorization_headers(self, method: str, url: str) -> dict[str, str]:
        try:
            with self._auth_lock:
                if self._credentials is None:
                    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
                    if self.credentials_file:
                        self._credentials, _ = google.auth.load_credentials_from_file(
                            self.credentials_file,
                            scopes=scopes,
                            quota_project_id=self.quota_project,
                        )
                    else:
                        self._credentials, _ = google.auth.default(
                            scopes=scopes,
                            quota_project_id=self.quota_project,
                        )
                headers: dict[str, str] = {}
                self._credentials.before_request(self._auth_request, method, url, headers)
                return headers
        except Exception as exc:
            raise ProviderError("Google Cloud credentials are not available") from exc

    async def _auth_headers(self, method: str, url: str) -> dict[str, str]:
        return await asyncio.to_thread(self._authorization_headers, method, url)

    async def aclose(self) -> None:
        await super().aclose()
        await asyncio.to_thread(self._auth_request.session.close)

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        params = {"languageCode": language} if language else None
        url = "https://texttospeech.googleapis.com/v1/voices"
        headers = await self._auth_headers("GET", url)
        r = await self._request(
            "GET",
            url,
            headers=headers,
            params=params,
        )
        data = self.parse_json(r)
        if not isinstance(data, dict):
            raise ProviderError("Google Cloud returned an invalid voice list")
        return [
            Voice(
                id=v.get("name", ""),
                name=v.get("name", ""),
                provider=self.id,
                language=(v.get("languageCodes") or [None])[0],
                gender=v.get("ssmlGender"),
                metadata={
                    "sample_rate_hz": v.get("naturalSampleRateHertz"),
                    "language_codes": v.get("languageCodes"),
                },
            )
            for v in data.get("voices", [])[:limit]
        ]

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        self.validate_request(req)
        if not req.voice_id:
            raise ProviderError("Google Cloud TTS requires voice_id")
        lang = req.language or "en-US"
        enc_map = {"mp3": "MP3", "wav": "LINEAR16", "ogg": "OGG_OPUS", "opus": "OGG_OPUS"}
        enc = enc_map.get(req.output_format.lower(), "MP3")
        payload: dict = {
            "input": {"text": req.text},
            "voice": {"languageCode": lang, "name": req.voice_id},
            "audioConfig": {"audioEncoding": enc},
        }
        if req.speed is not None:
            payload["audioConfig"]["speakingRate"] = req.speed
        url = "https://texttospeech.googleapis.com/v1/text:synthesize"
        headers = await self._auth_headers("POST", url)
        r = await self._request(
            "POST",
            url,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        try:
            data = self.parse_json(r)
            if not isinstance(data, dict):
                raise ProviderError("Google Cloud returned an invalid synthesis response")
            audio = base64.b64decode(data.get("audioContent", ""), validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProviderError("Google Cloud returned malformed audio") from exc
        self.validate_audio(audio)
        mime = {"MP3": "audio/mpeg", "LINEAR16": "audio/wav", "OGG_OPUS": "audio/ogg"}[enc]
        return SpeechResult(audio, mime, self.id)
