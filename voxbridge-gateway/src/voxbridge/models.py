from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass(slots=True)
class Voice:
    id: str
    name: str
    provider: str
    language: str | None = None
    gender: str | None = None
    description: str | None = None
    preview_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SpeechRequest:
    text: str
    voice_id: str | None = None
    model: str | None = None
    output_format: str = "mp3"
    instructions: str | None = None
    speed: float | None = None
    language: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SpeechResult:
    audio: bytes
    mime_type: str
    provider: str
    model: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DialogueSegment(BaseModel):
    """One ordered voice turn in a multi-voice dialogue."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5_000)
    voice_id: str = Field(min_length=1, max_length=500)
    instructions: str | None = Field(default=None, max_length=10_000)
    speed: float | None = Field(default=None, allow_inf_nan=False)
    language: str | None = Field(default=None, max_length=64)
    options: dict[str, Any] = Field(default_factory=dict)
    pause_after_ms: int = Field(default=250, ge=0, le=10_000)

    @field_validator("voice_id")
    @classmethod
    def normalize_voice_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("voice_id cannot be blank")
        return normalized


class MaterializedAudioMetadata(BaseModel):
    """Structured metadata for an exact generated-audio handoff."""

    model_config = ConfigDict(extra="forbid")

    synthetic_audio: Literal[True]
    materialized: Literal[True]
    file_name: str
    file_mime_type: str
    file_size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_resource_uri: str


class SpeechDeliveryMetadata(BaseModel):
    """Structured metadata shared by single- and multi-voice delivery results."""

    # Providers may add documented metadata such as duration or request tracing.
    # VoxBridge-owned delivery fields below remain validated and cannot be overridden.
    model_config = ConfigDict(extra="allow")

    synthetic_audio: Literal[True]
    provider: str
    model: str | None
    mime_type: str
    request_id: str | None
    delivery: Literal["playback", "file", "both"]
    playback_requested: bool
    inline_audio_included: bool
    app_resource_playback: bool
    file_resource_included: bool
    file_size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_name: str | None = None
    file_mime_type: str | None = None
    resource_uri: str | None = None
    materialize_resource_uri: str | None = None
    materialize_max_bytes: int | None = Field(default=None, ge=1)
    download_expires_at: str | None = None
