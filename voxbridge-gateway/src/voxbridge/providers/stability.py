from __future__ import annotations

from typing import Any

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
    default_music_model = "stable-audio-2.5"
    default_music_length_ms = 30_000
    music_models = ("stable-audio-2.5",)
    music_supported_formats = ("mp3", "wav")
    music_duration_range_ms = (1_000, 190_000)
    supports_music_composition_plans = False
    default_sound_effect_model = "stable-audio-2.5"
    default_sound_effect_duration_seconds = 5.0
    sound_effect_models = ("stable-audio-2.5",)
    sound_effect_supported_formats = ("mp3", "wav")
    sound_effect_duration_range_seconds = (1.0, 190.0)
    max_sound_effect_prompt_chars = 10_000
    supports_sound_effect_loop = False
    supports_sound_effect_prompt_influence = False
    max_audio_prompt_chars = 10_000

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 60.0,
        generation_timeout: float = 300.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key
        self.generation_timeout = generation_timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError("Stability AI is not configured")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "audio/*",
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
    ) -> None:
        if not prompt.strip():
            raise ProviderError("Stability AI audio prompt cannot be empty")
        if len(prompt) > self.max_audio_prompt_chars:
            raise ProviderError("Stability AI audio prompt accepts at most 10,000 characters")
        if model not in self.music_models:
            raise ProviderError("Stability AI audio model must be stable-audio-2.5")
        if output_format not in self.music_supported_formats:
            raise ProviderError("Stability AI audio output must be mp3 or wav")
        if duration_seconds is not None:
            if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)):
                raise ProviderError("Stability AI audio duration must be a number")
            if not 1 <= duration_seconds <= 190:
                raise ProviderError("Stability AI audio duration must be between 1 and 190 seconds")
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
        )
        if req.loop:
            raise ProviderError("Stability AI Stable Audio does not expose seamless looping")
        if req.prompt_influence is not None:
            raise ProviderError("Stability AI Stable Audio does not expose prompt_influence")

    async def _generate_audio(
        self,
        *,
        prompt: str,
        duration_seconds: float,
        model: str,
        output_format: str,
        seed: int | None,
    ) -> SpeechResult:
        multipart: dict[str, tuple[None, str]] = {
            "prompt": (None, prompt),
            "duration": (None, str(duration_seconds)),
            "model": (None, model),
            "output_format": (None, output_format),
        }
        if seed is not None:
            multipart["seed"] = (None, str(seed))
        try:
            r = await self._request(
                "POST",
                "https://api.stability.ai/v2beta/audio/stable-audio-2/text-to-audio",
                headers=self._headers(),
                files=multipart,
                timeout=self.generation_timeout,
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
                "credits_per_successful_generation": 20,
            },
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
        )
        result.metadata.update({"sound_effect": True})
        return result
