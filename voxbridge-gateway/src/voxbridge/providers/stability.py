from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from typing import Any, ClassVar

from voxbridge.models import MusicRequest, SoundEffectRequest, SpeechRequest, SpeechResult

from .base import ProviderError, VoiceProvider


class StabilityAudioProvider(VoiceProvider):
    _wav_sample_rate_hz = 44_100
    _wav_channels = 2
    _wav_bytes_per_sample_ceiling = 4
    _wav_header_reserve_bytes = 1_024
    id = "stability"
    display_name = "Stability AI Stable Audio"
    capabilities = ("music_generation", "sound_effect_generation")
    supported_formats = ()
    max_text_chars = 0
    default_music_model = "stable-audio-3"
    default_music_length_ms = 30_000
    music_models = ("stable-audio-3", "stable-audio-2.5")
    music_supported_formats = ("mp3", "wav")
    music_duration_range_ms = (1_000, 380_000)
    music_model_formats: ClassVar[dict[str, tuple[str, ...]]] = {
        "stable-audio-3": ("mp3", "wav"),
        "stable-audio-2.5": ("mp3", "wav"),
    }
    music_model_duration_ranges_ms: ClassVar[dict[str, tuple[int, int] | None]] = {
        "stable-audio-3": (1_000, 380_000),
        "stable-audio-2.5": (1_000, 190_000),
    }
    music_model_details: ClassVar[dict[str, dict[str, Any]]] = {
        "stable-audio-3": {
            "status": "current",
            "api_mode": "asynchronous",
            "result_retention": "provider-managed temporary result",
        },
        "stable-audio-2.5": {
            "status": "compatibility",
            "api_mode": "synchronous",
        },
    }
    music_steps_range = (4, 8)
    music_cfg_scale_range = (1.0, 25.0)
    supports_music_composition_plans = False
    default_sound_effect_model = "stable-audio-3"
    default_sound_effect_duration_seconds = 5.0
    sound_effect_models = ("stable-audio-3", "stable-audio-2.5")
    sound_effect_supported_formats = ("mp3", "wav")
    sound_effect_duration_range_seconds = (1.0, 380.0)
    sound_effect_model_duration_ranges_seconds: ClassVar[dict[str, tuple[float, float] | None]] = {
        "stable-audio-3": (1.0, 380.0),
        "stable-audio-2.5": (1.0, 190.0),
    }
    sound_effect_model_details: ClassVar[dict[str, dict[str, Any]]] = music_model_details
    sound_effect_steps_range = (4, 8)
    sound_effect_cfg_scale_range = (1.0, 25.0)
    max_sound_effect_prompt_chars = 10_000
    supports_sound_effect_loop = False
    supports_sound_effect_prompt_influence = False
    max_audio_prompt_chars = 10_000

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 60.0,
        music_timeout: float = 300.0,
        sound_effect_timeout: float = 120.0,
        generation_timeout: float | None = None,
        result_poll_interval_seconds: float = 10.0,
        result_poll_max_attempts: int = 60,
        result_poll_max_backoff_seconds: float = 30.0,
        generation_resume_ttl_seconds: float = 900.0,
        generation_resume_max_items: int = 128,
    ) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key
        if generation_timeout is not None:
            # Backward-compatible alias for callers that configured the former shared timeout.
            music_timeout = generation_timeout
            sound_effect_timeout = generation_timeout
        self.music_timeout = music_timeout
        self.sound_effect_timeout = sound_effect_timeout
        self.result_poll_interval_seconds = max(0.0, result_poll_interval_seconds)
        self.result_poll_max_attempts = max(1, result_poll_max_attempts)
        self.result_poll_max_backoff_seconds = max(0.0, result_poll_max_backoff_seconds)
        self.generation_resume_ttl_seconds = max(1.0, generation_resume_ttl_seconds)
        self.generation_resume_max_items = max(1, generation_resume_max_items)
        self._generation_resume_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._generation_resume_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self, *, accept: str = "audio/*") -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("Stability AI is not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": accept,
        }

    @property
    def wav_duration_max_seconds_for_audio_limit(self) -> float:
        payload_bytes = max(0, self.max_audio_bytes - self._wav_header_reserve_bytes)
        bytes_per_second = (
            self._wav_sample_rate_hz * self._wav_channels * self._wav_bytes_per_sample_ceiling
        )
        return round(payload_bytes / bytes_per_second, 3)

    def info(self) -> dict[str, Any]:
        details = super().info()
        details["wav_duration_max_seconds_for_audio_limit"] = (
            self.wav_duration_max_seconds_for_audio_limit
        )
        return details

    async def generate(self, req: SpeechRequest) -> SpeechResult:
        raise ProviderError("Stability AI Stable Audio does not support text-to-speech")

    @staticmethod
    def _validate_seed(seed: int | None) -> None:
        if seed is None:
            return
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ProviderError("Stability AI seed must be an integer")
        if not 0 <= seed <= 4_294_967_294:
            raise ProviderError("Stability AI seed must be between 0 and 4294967294")

    def _validate_common(
        self,
        *,
        prompt: str,
        duration_seconds: float | None,
        model: str,
        output_format: str,
        seed: int | None,
        steps: int | None,
        cfg_scale: float | None,
    ) -> None:
        if not prompt.strip():
            raise ProviderError("Stability AI audio prompt cannot be empty")
        if len(prompt) > self.max_audio_prompt_chars:
            raise ProviderError("Stability AI audio prompt accepts at most 10,000 characters")
        if model not in self.music_models:
            raise ProviderError(
                "Stability AI audio model must be stable-audio-3 or stable-audio-2.5"
            )
        if output_format not in self.music_supported_formats:
            raise ProviderError("Stability AI audio output must be mp3 or wav")
        if duration_seconds is not None:
            if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)):
                raise ProviderError("Stability AI audio duration must be a number")
            duration_range_ms = self.music_model_duration_ranges_ms[model]
            minimum = duration_range_ms[0] / 1_000
            maximum = duration_range_ms[1] / 1_000
            if not minimum <= duration_seconds <= maximum:
                raise ProviderError(
                    "Stability AI audio duration must be between "
                    f"{minimum:g} and {maximum:g} seconds for {model}"
                )
            if (
                output_format == "wav"
                and duration_seconds > self.wav_duration_max_seconds_for_audio_limit
            ):
                safe_duration = self.wav_duration_max_seconds_for_audio_limit
                raise ProviderError(
                    "Stability AI WAV duration exceeds the current VoxBridge audio byte limit; "
                    f"use MP3 or a duration no longer than {safe_duration:g} seconds"
                )
        self._validate_seed(seed)
        if steps is not None:
            if isinstance(steps, bool) or not isinstance(steps, int):
                raise ProviderError("Stability AI steps must be an integer")
            if not 4 <= steps <= 8:
                raise ProviderError("Stability AI steps must be between 4 and 8")
        if cfg_scale is not None:
            if isinstance(cfg_scale, bool) or not isinstance(cfg_scale, (int, float)):
                raise ProviderError("Stability AI cfg_scale must be a number")
            if not 1 <= cfg_scale <= 25:
                raise ProviderError("Stability AI cfg_scale must be between 1 and 25")

    def validate_music_request(self, req: MusicRequest) -> None:
        if req.prompt is None:
            raise ProviderError("Stability AI music requires a prompt")
        if req.composition_plan is not None:
            raise ProviderError("Stability AI music does not support composition plans")
        duration_seconds = (
            req.music_length_ms / 1_000
            if req.music_length_ms is not None
            else (self.default_music_length_ms or 30_000) / 1_000
        )
        self._validate_common(
            prompt=req.prompt,
            duration_seconds=duration_seconds,
            model=req.model,
            output_format=req.output_format,
            seed=req.seed,
            steps=req.steps,
            cfg_scale=req.cfg_scale,
        )
        if req.negative_prompt is not None:
            raise ProviderError("Stability AI Stable Audio does not expose negative_prompt")
        if req.force_instrumental:
            raise ProviderError(
                "Stability AI Stable Audio does not expose force_instrumental; describe "
                "instrumental output in the prompt"
            )
        if req.finetune_id is not None:
            raise ProviderError("Stability AI Stable Audio does not support finetune_id")
        if req.sign_with_c2pa:
            raise ProviderError("Stability AI Stable Audio does not expose C2PA signing")

    def validate_sound_effect_request(self, req: SoundEffectRequest) -> None:
        duration_seconds = (
            req.duration_seconds
            if req.duration_seconds is not None
            else self.default_sound_effect_duration_seconds
        )
        self._validate_common(
            prompt=req.prompt,
            duration_seconds=duration_seconds,
            model=req.model,
            output_format=req.output_format,
            seed=req.seed,
            steps=req.steps,
            cfg_scale=req.cfg_scale,
        )
        if req.loop:
            raise ProviderError("Stability AI Stable Audio does not expose seamless looping")
        if req.prompt_influence is not None:
            raise ProviderError("Stability AI Stable Audio does not expose prompt_influence")

    @staticmethod
    def _generation_resume_key(
        endpoint: str,
        multipart: dict[str, tuple[None, str]],
    ) -> str:
        canonical_payload = {
            "endpoint": endpoint,
            "fields": {name: value for name, (_filename, value) in sorted(multipart.items())},
        }
        encoded = json.dumps(
            canonical_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _prune_generation_resume_cache(self, now: float) -> None:
        expired = [
            key
            for key, (_generation_id, expires_at) in self._generation_resume_cache.items()
            if expires_at <= now
        ]
        for key in expired:
            self._generation_resume_cache.pop(key, None)
        while len(self._generation_resume_cache) > self.generation_resume_max_items:
            self._generation_resume_cache.popitem(last=False)

    def _cached_generation_id(self, cache_key: str) -> str | None:
        now = time.monotonic()
        self._prune_generation_resume_cache(now)
        cached = self._generation_resume_cache.pop(cache_key, None)
        if cached is None:
            return None
        generation_id, expires_at = cached
        self._generation_resume_cache[cache_key] = (generation_id, expires_at)
        return generation_id

    def _remember_generation_id(self, cache_key: str, generation_id: str) -> None:
        now = time.monotonic()
        self._prune_generation_resume_cache(now)
        self._generation_resume_cache.pop(cache_key, None)
        self._generation_resume_cache[cache_key] = (
            generation_id,
            now + self.generation_resume_ttl_seconds,
        )
        self._prune_generation_resume_cache(now)

    def _forget_generation_id(self, cache_key: str) -> None:
        self._generation_resume_cache.pop(cache_key, None)

    async def _submit_audio_request(
        self,
        *,
        endpoint: str,
        multipart: dict[str, tuple[None, str]],
        asynchronous: bool,
        deadline: float,
    ):
        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise ProviderError(
                "Stability AI timed out before audio generation could be submitted",
                retryable=True,
            )
        try:
            return await self._request(
                "POST",
                endpoint,
                headers=self._headers(accept="application/json" if asynchronous else "audio/*"),
                files=multipart,
                timeout=remaining,
            )
        except ProviderError as exc:
            if exc.status_code == 403:
                raise ProviderError(
                    "Stability AI rejected the request because of credentials, access, or "
                    "content moderation",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            if exc.status_code in {400, 422}:
                raise ProviderError(
                    "Stability AI rejected the audio prompt or settings",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            raise

    async def _generate_audio(
        self,
        *,
        prompt: str,
        duration_seconds: float,
        model: str,
        output_format: str,
        seed: int | None,
        steps: int | None,
        cfg_scale: float | None,
        operation_timeout: float,
    ) -> SpeechResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + operation_timeout
        multipart: dict[str, tuple[None, str]] = {
            "prompt": (None, prompt),
            "duration": (None, str(duration_seconds)),
            "model": (None, model),
            "output_format": (None, output_format),
        }
        if seed is not None:
            multipart["seed"] = (None, str(seed))
        if steps is not None:
            multipart["steps"] = (None, str(steps))
        if cfg_scale is not None:
            multipart["cfg_scale"] = (None, str(cfg_scale))
        asynchronous = model == "stable-audio-3"
        endpoint = (
            "https://api.stability.ai/v2beta/audio/stable-audio/text-to-audio"
            if asynchronous
            else "https://api.stability.ai/v2beta/audio/stable-audio-2/text-to-audio"
        )
        generation_id: str | None = None
        if asynchronous:
            cache_key = self._generation_resume_key(endpoint, multipart)
            async with self._generation_resume_lock:
                generation_id = self._cached_generation_id(cache_key)
                if generation_id is None:
                    submission_response = await self._submit_audio_request(
                        endpoint=endpoint,
                        multipart=multipart,
                        asynchronous=True,
                        deadline=deadline,
                    )
                    if submission_response.status_code != 202:
                        raise ProviderError(
                            "Stability AI returned an unexpected Stable Audio 3 submission response"
                        )
                    submission = self.parse_json(submission_response)
                    candidate_id = submission.get("id") if isinstance(submission, dict) else None
                    if not isinstance(candidate_id, str) or not re.fullmatch(
                        r"[A-Za-z0-9_-]{64}", candidate_id
                    ):
                        raise ProviderError("Stability AI returned an invalid generation id")
                    generation_id = candidate_id
                    self._remember_generation_id(cache_key, generation_id)
            try:
                r = await self._poll_audio_result(generation_id, deadline=deadline)
            except ProviderError as exc:
                if not exc.retryable or exc.status_code == 404:
                    self._forget_generation_id(cache_key)
                raise
            else:
                self._forget_generation_id(cache_key)
        else:
            r = await self._submit_audio_request(
                endpoint=endpoint,
                multipart=multipart,
                asynchronous=False,
                deadline=deadline,
            )

        default_mime = "audio/wav" if output_format == "wav" else "audio/mpeg"
        content_type = r.headers.get("content-type")
        returned_mime = content_type.split(";")[0].strip().lower() if content_type else default_mime
        if returned_mime == "application/octet-stream":
            returned_mime = default_mime
        elif not returned_mime.startswith("audio/"):
            raise ProviderError("Stability AI returned an invalid audio response")
        mime = returned_mime
        self.validate_audio(r.content)
        return SpeechResult(
            audio=r.content,
            mime_type=mime,
            provider=self.id,
            model=model,
            request_id=r.headers.get("request-id") or r.headers.get("x-request-id"),
            metadata={
                "duration_seconds": duration_seconds,
                "seed": seed,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "generation_id": generation_id,
                "api_mode": "asynchronous" if asynchronous else "synchronous",
            },
        )

    def _poll_retry_delay(self, retry_streak: int) -> float:
        if self.result_poll_interval_seconds == 0 or self.result_poll_max_backoff_seconds == 0:
            return 0.0
        exponent = min(max(0, retry_streak - 1), 10)
        return min(
            self.result_poll_interval_seconds * (2**exponent),
            self.result_poll_max_backoff_seconds,
        )

    async def _poll_audio_result(self, generation_id: str, *, deadline: float):
        loop = asyncio.get_running_loop()
        result_url = f"https://api.stability.ai/v2beta/audio/results/{generation_id}"
        retry_streak = 0
        for _attempt in range(self.result_poll_max_attempts):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                response = await self._request(
                    "GET",
                    result_url,
                    headers=self._headers(),
                    timeout=remaining,
                )
            except ProviderError as exc:
                if exc.status_code == 404:
                    raise ProviderError(
                        "Stability AI could not find the submitted audio generation result",
                        status_code=404,
                        request_id=exc.request_id,
                    ) from exc
                if not exc.retryable:
                    raise
                retry_streak += 1
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(self._poll_retry_delay(retry_streak), remaining))
                continue
            if response.status_code == 200:
                return response
            if response.status_code != 202:
                raise ProviderError(
                    "Stability AI returned an unexpected audio generation result status"
                )
            retry_streak = 0
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self.result_poll_interval_seconds, remaining))
        raise ProviderError(
            "Stability AI timed out while waiting for generated audio",
            retryable=True,
        )

    async def generate_music(self, req: MusicRequest) -> SpeechResult:
        self.validate_music_request(req)
        duration_seconds = req.music_length_ms / 1_000 if req.music_length_ms is not None else 30.0
        result = await self._generate_audio(
            prompt=req.prompt or "",
            duration_seconds=duration_seconds,
            model=req.model,
            output_format=req.output_format,
            seed=req.seed,
            steps=req.steps,
            cfg_scale=req.cfg_scale,
            operation_timeout=self.music_timeout,
        )
        result.metadata.update(
            {
                "music": True,
                "music_mode": "prompt",
                "music_length_ms": int(duration_seconds * 1_000),
            }
        )
        return result

    async def generate_sound_effect(self, req: SoundEffectRequest) -> SpeechResult:
        self.validate_sound_effect_request(req)
        duration_seconds = req.duration_seconds if req.duration_seconds is not None else 5.0
        result = await self._generate_audio(
            prompt=req.prompt,
            duration_seconds=duration_seconds,
            model=req.model,
            output_format=req.output_format,
            seed=req.seed,
            steps=req.steps,
            cfg_scale=req.cfg_scale,
            operation_timeout=self.sound_effect_timeout,
        )
        result.metadata.update({"sound_effect": True})
        return result
