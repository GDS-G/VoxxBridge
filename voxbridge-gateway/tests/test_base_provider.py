from __future__ import annotations

import gzip
from collections.abc import Callable

import httpx
import pytest

from voxbridge.models import SpeechRequest, SpeechResult
from voxbridge.providers.base import ProviderError, VoiceProvider


class StubProvider(VoiceProvider):
    id = "stub"
    display_name = "Stub Voice"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def configured(self) -> bool:
        return True

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        return SpeechResult(b"audio", "audio/mpeg", self.id)


class FlexibleProvider(StubProvider):
    supported_formats = ("mp3", "wav")
    allowed_options = frozenset({"tone"})
    max_text_chars = 10
    supports_instructions = True
    supports_language = True
    supports_model = True
    supports_speed = True


class Base64StubProvider(StubProvider):
    base64_audio_response = True


def request(**overrides) -> SpeechRequest:
    values = {"text": "hello"}
    values.update(overrides)
    return SpeechRequest(**values)


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (request(text=" \t\n"), "Text cannot be empty"),
        (request(text="x" * 11), "accepts at most 10 characters"),
        (request(output_format="flac"), "does not support 'flac' output"),
        (request(speed=0.24), "Stub Voice speed must be between 0.25 and 4"),
        (request(speed=4.01), "Stub Voice speed must be between 0.25 and 4"),
        (request(options={"unknown": True}), r"Unsupported Stub Voice option\(s\): unknown"),
    ],
)
async def test_request_validation_rejects_invalid_values(candidate, message):
    provider = FlexibleProvider()
    try:
        with pytest.raises(ProviderError, match=message):
            provider.validate_request(candidate)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("instructions", "Whisper", "does not support delivery instructions"),
        ("language", "en-US", "does not expose an explicit language control"),
        ("model", "custom", "does not expose model selection"),
        ("speed", 1.2, "does not support speed control"),
    ],
)
async def test_request_validation_rejects_unadvertised_controls(field, value, message):
    provider = StubProvider()
    try:
        with pytest.raises(ProviderError, match=message):
            provider.validate_request(request(**{field: value}))
    finally:
        await provider.aclose()


async def test_request_validation_accepts_every_advertised_control():
    provider = FlexibleProvider()
    try:
        provider.validate_request(
            request(
                output_format=" WAV ",
                instructions="Warmly",
                language="en-US",
                model="model-1",
                speed=1.5,
                options={"tone": "bright"},
            )
        )
    finally:
        await provider.aclose()


async def use_transport(provider: VoiceProvider, handler: Callable) -> None:
    await provider.client.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("status", "expected", "retryable"),
    [
        (400, r"Stub Voice rejected the request \(HTTP 400\)", False),
        (401, "Stub Voice rejected its configured credentials", False),
        (403, "Stub Voice rejected its configured credentials", False),
        (429, "Stub Voice rate limit or quota was reached", True),
        (500, "Stub Voice is temporarily unavailable", True),
        (503, "Stub Voice is temporarily unavailable", True),
    ],
)
async def test_provider_http_errors_are_sanitized(status, expected, retryable):
    secret = "super-secret-upstream-detail"
    provider = StubProvider()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": secret},
            headers={"x-request-id": "request-123"},
        )

    await use_transport(provider, handler)
    try:
        with pytest.raises(ProviderError, match=expected) as caught:
            await provider._request("GET", "https://provider.invalid/test")
    finally:
        await provider.aclose()

    error = caught.value
    assert secret not in str(error)
    assert "request-123" in str(error)
    assert error.status_code == status
    assert error.request_id == "request-123"
    assert error.retryable is retryable


async def test_request_id_is_bounded_before_appearing_in_an_error():
    provider = StubProvider()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, headers={"request-id": "r" * 500})

    await use_transport(provider, handler)
    try:
        with pytest.raises(ProviderError) as caught:
            await provider._request("GET", "https://provider.invalid/test")
    finally:
        await provider.aclose()

    assert caught.value.request_id == "r" * 200
    assert "r" * 201 not in str(caught.value)


@pytest.mark.parametrize(
    ("upstream_error", "expected"),
    [
        (httpx.ReadTimeout("secret timeout detail"), "Stub Voice timed out"),
        (httpx.ConnectError("secret connection detail"), "Stub Voice could not be reached"),
    ],
)
async def test_transport_errors_are_sanitized_and_retryable(upstream_error, expected):
    provider = StubProvider()

    def handler(_: httpx.Request) -> httpx.Response:
        raise upstream_error

    await use_transport(provider, handler)
    try:
        with pytest.raises(ProviderError, match=expected) as caught:
            await provider._request("GET", "https://provider.invalid/test")
    finally:
        await provider.aclose()

    assert "secret" not in str(caught.value)
    assert caught.value.retryable is True


async def test_response_size_limit_is_enforced():
    provider = StubProvider(max_response_bytes=3)

    class ChunkedStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        async def __aiter__(self):
            yield b"tw"
            yield b"o!"

        async def aclose(self) -> None:
            self.closed = True

    stream = ChunkedStream()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    await use_transport(provider, handler)
    try:
        with pytest.raises(ProviderError, match="returned an oversized response"):
            await provider._request("GET", "https://provider.invalid/test")
    finally:
        await provider.aclose()

    assert stream.closed is True


async def test_declared_response_size_limit_is_enforced_before_body_is_read():
    provider = StubProvider(max_response_bytes=3)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "4"}, content=b"four")

    await use_transport(provider, handler)
    try:
        with pytest.raises(ProviderError, match="returned an oversized response"):
            await provider._request("GET", "https://provider.invalid/test")
    finally:
        await provider.aclose()


async def test_streamed_compressed_response_is_decoded_once_and_headers_are_normalized():
    provider = StubProvider(max_response_bytes=100)
    compressed = gzip.compress(b"audio-bytes")

    class GzipStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield compressed[:3]
            yield compressed[3:]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-encoding": "gzip",
                "transfer-encoding": "chunked",
                "content-type": "audio/mpeg",
            },
            stream=GzipStream(),
        )

    await use_transport(provider, handler)
    try:
        response = await provider._request("GET", "https://provider.invalid/test")
    finally:
        await provider.aclose()

    assert response.content == b"audio-bytes"
    assert "content-encoding" not in response.headers
    assert "transfer-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(b"audio-bytes"))
    assert response.headers["content-type"] == "audio/mpeg"


async def test_base64_json_envelope_can_exceed_the_decoded_audio_limit():
    provider = Base64StubProvider()
    provider.configure_audio_limit(3)
    payload = b'{"audio":"YWJj"}'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    await use_transport(provider, handler)
    try:
        response = await provider._request("GET", "https://provider.invalid/test")
        provider.validate_audio(b"abc")
    finally:
        await provider.aclose()

    assert len(payload) > provider.max_audio_bytes
    assert response.content == payload


async def test_invalid_json_error_does_not_echo_the_upstream_body():
    provider = StubProvider()
    response = httpx.Response(
        200,
        content=b'{"secret":"upstream-sensitive-value"',
        request=httpx.Request("GET", "https://provider.invalid/test"),
    )
    try:
        with pytest.raises(ProviderError, match="Stub Voice returned invalid JSON") as caught:
            provider.parse_json(response)
    finally:
        await provider.aclose()

    assert "upstream-sensitive-value" not in str(caught.value)


@pytest.mark.parametrize(
    ("audio", "message"),
    [(b"", "returned empty audio"), (b"four", "returned oversized audio")],
)
async def test_audio_validation(audio, message):
    provider = StubProvider(max_response_bytes=3)
    try:
        with pytest.raises(ProviderError, match=message):
            provider.validate_audio(audio)
    finally:
        await provider.aclose()
