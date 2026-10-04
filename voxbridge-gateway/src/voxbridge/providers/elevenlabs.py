from __future__ import annotations

from typing import ClassVar

from voxbridge.models import (
    MusicRequest,
    SoundEffectRequest,
    SpeechRequest,
    SpeechResult,
    Voice,
)

from .base import ProviderError, VoiceProvider


class ElevenLabsProvider(VoiceProvider):
    id = "elevenlabs"
    display_name = "ElevenLabs"
    default_model = "eleven_multilingual_v2"
    capabilities = (
        "text_to_speech",
        "list_voices",
        "music_generation",
        "sound_effect_generation",
    )
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
    default_music_model = "music_v2_5"
    default_music_length_ms = 30_000
    music_models = ("music_v1", "music_v2", "music_v2_5")
    music_supported_formats = ("mp3",)
    music_duration_range_ms = (3_000, 600_000)
    supports_music_composition_plans = True
    default_sound_effect_model = "eleven_text_to_sound_v2"
    default_sound_effect_prompt_influence = 0.3
    sound_effect_models = ("eleven_text_to_sound_v2",)
    sound_effect_supported_formats = ("mp3",)
    sound_effect_duration_range_seconds = (0.5, 30.0)
    max_sound_effect_prompt_chars = 450
    supports_sound_effect_loop = True
    supports_sound_effect_prompt_influence = True

    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 60.0,
        music_timeout: float = 300.0,
        sound_effect_timeout: float = 120.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.api_key = api_key
        self.music_timeout = music_timeout
        self.sound_effect_timeout = sound_effect_timeout

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

    def validate_music_request(self, req: MusicRequest) -> None:
        if req.prompt is not None and not req.prompt.strip():
            raise ProviderError("ElevenLabs music prompt cannot be empty")
        has_prompt = req.prompt is not None
        has_plan = req.composition_plan is not None
        if has_prompt == has_plan:
            raise ProviderError(
                "ElevenLabs music requires exactly one of prompt or composition_plan"
            )
        if req.prompt is not None and len(req.prompt) > 4_100:
            raise ProviderError("ElevenLabs music prompt accepts at most 4,100 characters")
        if req.composition_plan is not None and (
            not isinstance(req.composition_plan, dict) or not req.composition_plan
        ):
            raise ProviderError("ElevenLabs composition_plan must be a non-empty object")
        if req.negative_prompt is not None:
            raise ProviderError("ElevenLabs music does not expose a separate negative_prompt")
        if req.model not in self.music_models:
            raise ProviderError("ElevenLabs music model must be music_v1, music_v2, or music_v2_5")
        if req.output_format not in self.music_supported_formats:
            raise ProviderError("ElevenLabs music output must be mp3")
        if req.music_length_ms is not None:
            if isinstance(req.music_length_ms, bool) or not isinstance(req.music_length_ms, int):
                raise ProviderError("ElevenLabs music_length_ms must be an integer")
            minimum, maximum = self.music_duration_range_ms
            if not minimum <= req.music_length_ms <= maximum:
                raise ProviderError(
                    f"ElevenLabs music_length_ms must be between {minimum} and {maximum}"
                )
            if req.prompt is None:
                raise ProviderError("ElevenLabs music_length_ms can only be used with a prompt")
        if not isinstance(req.force_instrumental, bool):
            raise ProviderError("ElevenLabs force_instrumental must be a boolean")
        if req.force_instrumental and req.prompt is None:
            raise ProviderError("ElevenLabs force_instrumental can only be used with a prompt")
        if req.seed is not None:
            if isinstance(req.seed, bool) or not isinstance(req.seed, int):
                raise ProviderError("ElevenLabs music seed must be an integer")
            if not 0 <= req.seed <= 2_147_483_647:
                raise ProviderError("ElevenLabs music seed must be between 0 and 2147483647")
            if req.prompt is not None:
                raise ProviderError("ElevenLabs music seed cannot be used with a prompt")
        if req.finetune_id is not None and (
            not req.finetune_id.strip() or len(req.finetune_id) > 100
        ):
            raise ProviderError("ElevenLabs music finetune_id must contain 1 to 100 characters")
        if not isinstance(req.respect_sections_durations, bool):
            raise ProviderError("ElevenLabs respect_sections_durations must be a boolean")
        if not isinstance(req.sign_with_c2pa, bool):
            raise ProviderError("ElevenLabs sign_with_c2pa must be a boolean")

    async def generate_music(self, req: MusicRequest) -> SpeechResult:
        self.validate_music_request(req)
        payload: dict = {
            "model_id": req.model,
            "sign_with_c2pa": req.sign_with_c2pa,
        }
        if req.prompt is not None:
            payload["prompt"] = req.prompt
            if req.music_length_ms is not None:
                payload["music_length_ms"] = req.music_length_ms
            payload["force_instrumental"] = req.force_instrumental
        else:
            payload["composition_plan"] = req.composition_plan
            payload["respect_sections_durations"] = req.respect_sections_durations
            if req.seed is not None:
                payload["seed"] = req.seed
        if req.finetune_id is not None:
            payload["finetune_id"] = req.finetune_id.strip()

        try:
            r = await self._request(
                "POST",
                "https://api.elevenlabs.io/v1/music",
                headers={**self._headers(), "Content-Type": "application/json"},
                params={"output_format": "auto"},
                json=payload,
                timeout=self.music_timeout,
            )
        except ProviderError as exc:
            if exc.status_code == 403:
                raise ProviderError(
                    "ElevenLabs rejected its credentials or Music API entitlement",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            if exc.status_code == 422:
                raise ProviderError(
                    "ElevenLabs rejected the music prompt, composition plan, or settings",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            raise

        default_mime = "audio/mpeg"
        content_type = r.headers.get("content-type")
        returned_mime = content_type.split(";")[0].strip().lower() if content_type else default_mime
        if returned_mime == "application/octet-stream":
            returned_mime = default_mime
        elif not returned_mime.startswith("audio/"):
            raise ProviderError("ElevenLabs returned an invalid music audio response")
        mime = returned_mime
        self.validate_audio(r.content)
        return SpeechResult(
            audio=r.content,
            mime_type=mime,
            provider=self.id,
            model=req.model,
            request_id=r.headers.get("request-id"),
            metadata={
                "music": True,
                "music_mode": "prompt" if req.prompt is not None else "composition_plan",
                "music_length_ms": req.music_length_ms,
                "force_instrumental": req.force_instrumental if req.prompt is not None else None,
                "song_id": r.headers.get("song-id"),
            },
        )

    def validate_sound_effect_request(self, req: SoundEffectRequest) -> None:
        if not req.prompt.strip():
            raise ProviderError("ElevenLabs sound-effect prompt cannot be empty")
        if len(req.prompt) > self.max_sound_effect_prompt_chars:
            raise ProviderError("ElevenLabs sound-effect prompt accepts at most 450 characters")
        if req.model not in self.sound_effect_models:
            raise ProviderError("ElevenLabs sound-effect model must be eleven_text_to_sound_v2")
        if req.output_format not in self.sound_effect_supported_formats:
            raise ProviderError("ElevenLabs sound-effect output must be mp3")
        if req.duration_seconds is not None:
            if isinstance(req.duration_seconds, bool) or not isinstance(
                req.duration_seconds, (int, float)
            ):
                raise ProviderError("ElevenLabs sound-effect duration_seconds must be a number")
            minimum, maximum = self.sound_effect_duration_range_seconds
            if not minimum <= req.duration_seconds <= maximum:
                raise ProviderError(
                    "ElevenLabs sound-effect duration_seconds must be between 0.5 and 30"
                )
        if not isinstance(req.loop, bool):
            raise ProviderError("ElevenLabs sound-effect loop must be a boolean")
        if req.seed is not None:
            raise ProviderError("ElevenLabs sound effects do not expose a seed control")
        if req.prompt_influence is not None and (
            isinstance(req.prompt_influence, bool)
            or not isinstance(req.prompt_influence, (int, float))
        ):
            raise ProviderError(
                "ElevenLabs sound-effect prompt_influence must be a number between 0 and 1"
            )
        if req.prompt_influence is not None and not 0 <= req.prompt_influence <= 1:
            raise ProviderError(
                "ElevenLabs sound-effect prompt_influence must be a number between 0 and 1"
            )

    async def generate_sound_effect(self, req: SoundEffectRequest) -> SpeechResult:
        self.validate_sound_effect_request(req)
        prompt_influence = 0.3 if req.prompt_influence is None else req.prompt_influence
        payload: dict = {
            "text": req.prompt,
            "loop": req.loop,
            "prompt_influence": prompt_influence,
            "model_id": req.model,
        }
        if req.duration_seconds is not None:
            payload["duration_seconds"] = req.duration_seconds

        try:
            r = await self._request(
                "POST",
                "https://api.elevenlabs.io/v1/sound-generation",
                headers={**self._headers(), "Content-Type": "application/json"},
                params={"output_format": "mp3_44100_128"},
                json=payload,
                timeout=self.sound_effect_timeout,
            )
        except ProviderError as exc:
            if exc.status_code == 403:
                raise ProviderError(
                    "ElevenLabs rejected its credentials or Sound Effects API entitlement",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            if exc.status_code == 422:
                raise ProviderError(
                    "ElevenLabs rejected the sound-effect prompt or settings",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            raise

        default_mime = "audio/mpeg"
        content_type = r.headers.get("content-type")
        returned_mime = content_type.split(";")[0].strip().lower() if content_type else default_mime
        if returned_mime == "application/octet-stream":
            returned_mime = default_mime
        elif not returned_mime.startswith("audio/"):
            raise ProviderError("ElevenLabs returned an invalid sound-effect audio response")
        mime = returned_mime
        self.validate_audio(r.content)
        return SpeechResult(
            audio=r.content,
            mime_type=mime,
            provider=self.id,
            model=req.model,
            request_id=r.headers.get("request-id"),
            metadata={
                "sound_effect": True,
                "duration_seconds": req.duration_seconds,
                "loop": req.loop,
                "prompt_influence": prompt_influence,
                "character_cost": r.headers.get("character-cost"),
            },
        )
