from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
