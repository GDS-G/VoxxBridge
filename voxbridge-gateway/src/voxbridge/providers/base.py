from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from voxbridge import __version__
from voxbridge.models import SpeechRequest, SpeechResult, Voice


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable


class VoiceProvider(ABC):
    id: str
    display_name: str
    default_model: str | None = None
    capabilities: tuple[str, ...] = ("text_to_speech",)
    supported_formats: tuple[str, ...] = ("mp3",)
    allowed_options: frozenset[str] = frozenset()
    max_text_chars: int = 3_000
    supports_instructions: bool = False
    supports_language: bool = False
    supports_model: bool = False
    supports_speed: bool = False
    min_speed: float = 0.25
    max_speed: float = 4.0
    instructions_note: str | None = None
    control_notes: ClassVar[dict[str, str]] = {}
    base64_audio_response: bool = False

    def __init__(
        self, *, timeout: float = 60.0, max_response_bytes: int = 20 * 1024 * 1024
    ) -> None:
        self.max_response_bytes = max_response_bytes
        self.max_audio_bytes = max_response_bytes
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"User-Agent": f"VoxBridge/{__version__}"},
        )

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    async def list_voices(self, *, language: str | None = None, limit: int = 50) -> list[Voice]:
        return []

    @abstractmethod
    async def generate(self, req: SpeechRequest) -> SpeechResult: ...

    async def aclose(self) -> None:
        await self.client.aclose()

    def configure_audio_limit(self, max_audio_bytes: int) -> None:
        self.max_audio_bytes = max_audio_bytes
        if self.base64_audio_response:
            base64_bytes = 4 * ((max_audio_bytes + 2) // 3)
            self.max_response_bytes = base64_bytes + 256 * 1024
        else:
            self.max_response_bytes = max_audio_bytes

    def validate_request(self, req: SpeechRequest) -> None:
        if not req.text.strip():
            raise ProviderError("Text cannot be empty")
        if len(req.text) > self.max_text_chars:
            raise ProviderError(
                f"{self.display_name} accepts at most {self.max_text_chars:,} characters per request"
            )
        output_format = req.output_format.strip().lower()
        if output_format not in self.supported_formats:
            allowed = ", ".join(self.supported_formats)
            raise ProviderError(
                f"{self.display_name} does not support '{output_format}' output; use {allowed}"
            )
        if req.instructions and not self.supports_instructions:
            raise ProviderError(f"{self.display_name} does not support delivery instructions")
        if req.language and not self.supports_language:
            raise ProviderError(f"{self.display_name} does not expose an explicit language control")
        if req.model and not self.supports_model:
            raise ProviderError(f"{self.display_name} does not expose model selection")
        if req.speed is not None:
            if not self.supports_speed:
                raise ProviderError(f"{self.display_name} does not support speed control")
            if not self.min_speed <= req.speed <= self.max_speed:
                raise ProviderError(
                    f"{self.display_name} speed must be between "
                    f"{self.min_speed:g} and {self.max_speed:g}"
                )
        unknown_options = set(req.options) - self.allowed_options
        if unknown_options:
            names = ", ".join(sorted(unknown_options))
            raise ProviderError(f"Unsupported {self.display_name} option(s): {names}")

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            request = self.client.build_request(method, url, **kwargs)
            response = await self.client.send(request, stream=True)
            try:
                await self._raise_for_status(response)
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = 0
                    if declared_size > self.max_response_bytes:
                        raise ProviderError(f"{self.display_name} returned an oversized response")

                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_response_bytes:
                        raise ProviderError(f"{self.display_name} returned an oversized response")
                    chunks.append(chunk)
                content = b"".join(chunks)
                decoded_headers = [
                    (name, value)
                    for name, value in response.headers.multi_items()
                    if name.lower()
                    not in {"content-encoding", "content-length", "transfer-encoding"}
                ]
                return httpx.Response(
                    status_code=response.status_code,
                    headers=decoded_headers,
                    content=content,
                    request=request,
                    extensions=response.extensions,
                )
            finally:
                await response.aclose()
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.display_name} timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"{self.display_name} could not be reached", retryable=True
            ) from exc

    async def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        request_id = self._request_id(response.headers)
        if response.status_code in {401, 403}:
            message = f"{self.display_name} rejected its configured credentials"
        elif response.status_code == 429:
            message = f"{self.display_name} rate limit or quota was reached"
        elif response.status_code >= 500:
            message = f"{self.display_name} is temporarily unavailable"
        else:
            message = f"{self.display_name} rejected the request (HTTP {response.status_code})"
        if request_id:
            message += f" [request_id={request_id}]"
        raise ProviderError(
            message,
            status_code=response.status_code,
            request_id=request_id,
            retryable=response.status_code == 429 or response.status_code >= 500,
        )

    @staticmethod
    def _request_id(headers: Mapping[str, str]) -> str | None:
        for name in ("request-id", "x-request-id", "dg-request-id", "x-ms-requestid"):
            value = headers.get(name)
            if value:
                return value[:200]
        return None

    def validate_audio(self, audio: bytes) -> None:
        if not audio:
            raise ProviderError(f"{self.display_name} returned empty audio")
        if len(audio) > self.max_audio_bytes:
            raise ProviderError(f"{self.display_name} returned oversized audio")

    def parse_json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderError(f"{self.display_name} returned invalid JSON") from exc

    def info(self) -> dict[str, Any]:
        gateway_formats = [
            output_format for output_format in self.supported_formats if output_format != "pcm"
        ]
        return {
            "id": self.id,
            "name": self.display_name,
            "configured": self.configured,
            "default_model": self.default_model,
            "capabilities": list(self.capabilities),
            "supported_formats": gateway_formats,
            "file_delivery_formats": gateway_formats,
            "max_text_chars": self.max_text_chars,
            "supports_instructions": self.supports_instructions,
            "instructions_note": self.instructions_note,
            "control_notes": self.control_notes,
            "supports_language": self.supports_language,
            "supports_model": self.supports_model,
            "supports_speed": self.supports_speed,
            "speed_range": [self.min_speed, self.max_speed] if self.supports_speed else None,
            "allowed_options": sorted(self.allowed_options),
        }
