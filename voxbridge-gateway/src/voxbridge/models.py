from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
