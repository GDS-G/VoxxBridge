from __future__ import annotations

import base64
import binascii
from typing import Any

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
    music_models = ("lyria-002",)
    music_supported_formats = ("wav",)
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
            raise ProviderError("Google Cloud music model must be lyria-002")
        if req.output_format not in self.music_supported_formats:
            raise ProviderError("Google Cloud Lyria output must be wav")
        if req.music_length_ms is not None:
            raise ProviderError("Google Cloud Lyria uses a provider-fixed clip duration")
        if not isinstance(req.force_instrumental, bool):
            raise ProviderError("Google Cloud Lyria force_instrumental must be a boolean")
        if req.seed is not None:
            if isinstance(req.seed, bool) or not isinstance(req.seed, int):
                raise ProviderError("Google Cloud Lyria seed must be an integer")
            if not 0 <= req.seed <= 4_294_967_294:
                raise ProviderError("Google Cloud Lyria seed must be between 0 and 4294967294")
        if req.negative_prompt is not None and not req.negative_prompt.strip():
            raise ProviderError("Google Cloud Lyria negative_prompt cannot be blank")
        if req.finetune_id is not None:
            raise ProviderError("Google Cloud Lyria does not support finetune_id")
        if req.sign_with_c2pa:
            raise ProviderError("Google Cloud Lyria does not expose C2PA signing")

    def _music_url(self, model: str) -> str:
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
