from __future__ import annotations

import base64
import binascii
from typing import Any, ClassVar

from voxbridge.models import MusicRequest, SpeechResult

from .base import ProviderError
from .google_cloud import GoogleCloudProvider


class GoogleLyriaProvider(GoogleCloudProvider):
    id = "google-lyria"
    display_name = "Google Cloud Lyria"
    capabilities = ("music_generation",)
    supported_formats = ()
    max_text_chars = 0
    supports_language = False
    supports_speed = False
    default_music_model = "lyria-002"
    music_models = ("lyria-002", "lyria-3-clip-preview", "lyria-3-pro-preview")
    music_supported_formats = ("wav", "mp3")
    music_model_formats: ClassVar[dict[str, tuple[str, ...]]] = {
        "lyria-002": ("wav",),
        "lyria-3-clip-preview": ("mp3",),
        "lyria-3-pro-preview": ("mp3",),
    }
    music_model_duration_ranges_ms: ClassVar[dict[str, tuple[int, int] | None]] = {
        "lyria-002": None,
        "lyria-3-clip-preview": None,
        "lyria-3-pro-preview": None,
    }
    music_model_details: ClassVar[dict[str, dict[str, Any]]] = {
        "lyria-002": {
            "status": "stable",
            "duration_control": "provider_fixed",
            "output": "instrumental WAV",
        },
        "lyria-3-clip-preview": {
            "status": "preview",
            "maximum_duration_seconds": 30,
            "duration_control": "prompt_based",
            "prompt_controls": ["BPM", "intensity", "lyrics", "instrumental mode"],
            "output": "MP3",
        },
        "lyria-3-pro-preview": {
            "status": "preview",
            "maximum_duration_seconds": 184,
            "duration_control": "prompt_based",
            "prompt_controls": [
                "duration",
                "BPM",
                "intensity",
                "lyrics",
                "instrumental mode",
                "song structure",
            ],
            "output": "MP3",
        },
    }
    music_duration_range_ms = None
    supports_music_composition_plans = False

    def __init__(
        self,
        project: str | None,
        *,
        credentials_file: str | None = None,
        enabled: bool = False,
        location: str = "global",
        timeout: float = 60.0,
        music_timeout: float = 300.0,
    ) -> None:
        if location != "global":
            raise ValueError("Google Cloud Lyria location must be global")
        super().__init__(
            project,
            credentials_file=credentials_file,
            enabled=enabled,
            timeout=timeout,
        )
        self.location = location
        self.music_timeout = music_timeout

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.quota_project)

    def validate_music_request(self, req: MusicRequest) -> None:
        if req.prompt is None or not req.prompt.strip():
            raise ProviderError("Google Cloud Lyria requires a non-empty music prompt")
        if req.composition_plan is not None:
            raise ProviderError("Google Cloud Lyria does not support composition plans")
        if req.model not in self.music_models:
            raise ProviderError(
                "Google Cloud music model must be lyria-002, lyria-3-clip-preview, or "
                "lyria-3-pro-preview"
            )
        model_formats = self.music_model_formats[req.model]
        if req.output_format not in model_formats:
            allowed = " or ".join(model_formats)
            raise ProviderError(f"Google Cloud {req.model} output must be {allowed}")
        if req.music_length_ms is not None:
            if req.model == "lyria-002":
                raise ProviderError("Google Cloud Lyria 2 uses a provider-fixed clip duration")
            raise ProviderError(
                "Google Cloud Lyria 3 duration is prompt-based; describe the desired length "
                "in the prompt"
            )
        if not isinstance(req.force_instrumental, bool):
            raise ProviderError("Google Cloud Lyria force_instrumental must be a boolean")
        if req.model == "lyria-002" and req.seed is not None:
            if isinstance(req.seed, bool) or not isinstance(req.seed, int):
                raise ProviderError("Google Cloud Lyria seed must be an integer")
            if not 0 <= req.seed <= 4_294_967_294:
                raise ProviderError("Google Cloud Lyria seed must be between 0 and 4294967294")
        if req.model != "lyria-002" and req.seed is not None:
            raise ProviderError("Google Cloud Lyria 3 does not expose an explicit seed control")
        if req.negative_prompt is not None:
            if req.model != "lyria-002":
                raise ProviderError(
                    "Google Cloud Lyria 3 does not expose a separate negative_prompt; "
                    "describe exclusions in the prompt"
                )
            if not req.negative_prompt.strip():
                raise ProviderError("Google Cloud Lyria negative_prompt cannot be blank")
        if req.finetune_id is not None:
            raise ProviderError("Google Cloud Lyria does not support finetune_id")
        if req.sign_with_c2pa:
            raise ProviderError(
                "Google Cloud Lyria does not expose C2PA signing as a request-time control"
            )
        if req.steps is not None:
            raise ProviderError("Google Cloud Lyria does not expose a steps control")
        if req.cfg_scale is not None:
            raise ProviderError("Google Cloud Lyria does not expose a cfg_scale control")

    def _music_url(self, model: str) -> str:
        if model != "lyria-002":
            return (
                f"https://aiplatform.googleapis.com/v1/projects/{self.quota_project}/"
                f"locations/{self.location}/interactions"
            )
        return (
            f"https://aiplatform.googleapis.com/v1/projects/{self.quota_project}/"
            f"locations/{self.location}/"
            f"publishers/google/models/{model}:predict"
        )

    async def generate_music(self, req: MusicRequest) -> SpeechResult:
        self.validate_music_request(req)
        if not self.configured:
            raise ProviderError(
                "Google Cloud Lyria requires GOOGLE_CLOUD_MUSIC_ENABLED=true and "
                "GOOGLE_CLOUD_PROJECT"
            )

        url = self._music_url(req.model)
        headers = await self._auth_headers("POST", url)
        if req.model != "lyria-002":
            return await self._generate_lyria3(req, url=url, headers=headers)

        instance: dict[str, Any] = {"prompt": req.prompt}
        if req.negative_prompt is not None:
            instance["negative_prompt"] = req.negative_prompt
        if req.seed is not None:
            instance["seed"] = req.seed

        try:
            r = await self._request(
                "POST",
                url,
                headers={**headers, "Content-Type": "application/json"},
                json={"instances": [instance], "parameters": {}},
                timeout=self.music_timeout,
            )
        except ProviderError as exc:
            if exc.status_code == 403:
                raise ProviderError(
                    "Google Cloud rejected its credentials or Lyria access",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            if exc.status_code in {400, 422}:
                raise ProviderError(
                    "Google Cloud Lyria rejected the music prompt or settings",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            raise

        data = self.parse_json(r)
        if not isinstance(data, dict):
            raise ProviderError("Google Cloud Lyria returned an invalid response")
        predictions = data.get("predictions")
        if not isinstance(predictions, list) or len(predictions) != 1:
            raise ProviderError("Google Cloud Lyria returned an invalid audio result")
        prediction = predictions[0]
        if not isinstance(prediction, dict) or prediction.get("mimeType") != "audio/wav":
            raise ProviderError("Google Cloud Lyria returned an invalid audio result")
        try:
            audio = base64.b64decode(prediction.get("audioContent", ""), validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProviderError("Google Cloud Lyria returned malformed audio") from exc
        self.validate_audio(audio)
        return SpeechResult(
            audio=audio,
            mime_type="audio/wav",
            provider=self.id,
            model=req.model,
            request_id=r.headers.get("x-request-id") or r.headers.get("x-goog-request-id"),
            metadata={
                "music": True,
                "music_mode": "prompt",
                "duration_control": "provider_fixed",
                "force_instrumental": True,
                "seed": req.seed,
                "model_display_name": data.get("modelDisplayName"),
            },
        )

    async def _generate_lyria3(
        self,
        req: MusicRequest,
        *,
        url: str,
        headers: dict[str, str],
    ) -> SpeechResult:
        prompt = req.prompt or ""
        if req.force_instrumental:
            prompt = f"{prompt.rstrip()} Instrumental only; do not include vocals or lyrics."
        payload = {
            "model": req.model,
            "store": False,
            "input": [
                {
                    "type": "user_input",
                    "content": [{"type": "text", "text": prompt}],
                }
            ],
        }
        try:
            response = await self._request(
                "POST",
                url,
                headers={**headers, "Content-Type": "application/json; charset=utf-8"},
                json=payload,
                timeout=self.music_timeout,
            )
        except ProviderError as exc:
            if exc.status_code == 403:
                raise ProviderError(
                    "Google Cloud rejected its credentials or Lyria 3 preview access",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            if exc.status_code in {400, 422}:
                raise ProviderError(
                    "Google Cloud Lyria 3 rejected the music prompt or settings",
                    status_code=exc.status_code,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                ) from exc
            raise

        data = self.parse_json(response)
        if not isinstance(data, dict) or data.get("status") != "completed":
            raise ProviderError("Google Cloud Lyria 3 returned an incomplete response")
        steps = data.get("steps")
        if not isinstance(steps, list):
            raise ProviderError("Google Cloud Lyria 3 returned an invalid response")

        texts: list[str] = []
        audio_payloads: list[str] = []
        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "model_output":
                continue
            content = step.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                elif part.get("type") == "audio":
                    if part.get("mime_type") != "audio/mpeg" or not isinstance(
                        part.get("data"), str
                    ):
                        raise ProviderError("Google Cloud Lyria 3 returned invalid audio metadata")
                    audio_payloads.append(part["data"])
        if len(audio_payloads) != 1:
            raise ProviderError("Google Cloud Lyria 3 returned an invalid audio result")
        try:
            audio = base64.b64decode(audio_payloads[0], validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProviderError("Google Cloud Lyria 3 returned malformed audio") from exc
        self.validate_audio(audio)

        lyrics = texts[0] if len(texts) >= 2 else None
        description = texts[-1] if texts else None
        return SpeechResult(
            audio=audio,
            mime_type="audio/mpeg",
            provider=self.id,
            model=req.model,
            request_id=(
                response.headers.get("x-request-id") or response.headers.get("x-goog-request-id")
            ),
            metadata={
                "music": True,
                "music_mode": "prompt",
                "preview": True,
                "duration_control": "prompt_based",
                "force_instrumental": req.force_instrumental,
                "interaction_id": data.get("id"),
                "description": description,
                "lyrics": lyrics,
                "prompt_controls": self.music_model_details[req.model]["prompt_controls"],
            },
        )
