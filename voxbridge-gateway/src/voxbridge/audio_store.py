from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    data: bytes
    mime_type: str
    format_id: str
    file_name: str
    expires_at: float


class AudioArtifactStore:
    """Process-local, bounded store for short-lived MCP download resources."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_items: int,
        max_bytes: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._clock = clock
        self._items: OrderedDict[str, AudioArtifact] = OrderedDict()
        self._total_bytes = 0
        self._lock = RLock()

    def put(
        self,
        data: bytes,
        *,
        mime_type: str,
        format_id: str,
        file_name: str,
    ) -> tuple[str, AudioArtifact]:
        if not data:
            raise ValueError("audio artifact cannot be empty")
        if len(data) > self._max_bytes:
            raise ValueError("audio artifact exceeds the download cache byte limit")

        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            token = secrets.token_urlsafe(24)
            while token in self._items:  # pragma: no cover - cryptographically improbable
                token = secrets.token_urlsafe(24)
            artifact = AudioArtifact(
                data=data,
                mime_type=mime_type,
                format_id=format_id,
                file_name=file_name,
                expires_at=now + self._ttl_seconds,
            )
            self._items[token] = artifact
            self._total_bytes += len(data)
            self._evict_to_limits()
            return token, artifact

    def get(self, token: str) -> AudioArtifact | None:
        with self._lock:
            self._purge_expired(self._clock())
            artifact = self._items.get(token)
            if artifact is not None:
                self._items.move_to_end(token)
            return artifact

    def get_by_file_name(self, file_name: str) -> AudioArtifact | None:
        """Return the newest live artifact with an exact generated file name."""

        with self._lock:
            self._purge_expired(self._clock())
            for token in reversed(self._items):
                artifact = self._items[token]
                if artifact.file_name == file_name:
                    self._items.move_to_end(token)
                    return artifact
            return None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def item_count(self) -> int:
        with self._lock:
            return len(self._items)

    def _purge_expired(self, now: float) -> None:
        expired = [token for token, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            self._remove(token)

    def _evict_to_limits(self) -> None:
        while len(self._items) > self._max_items or self._total_bytes > self._max_bytes:
            token = next(iter(self._items))
            self._remove(token)

    def _remove(self, token: str) -> None:
        artifact = self._items.pop(token, None)
        if artifact is not None:
            self._total_bytes -= len(artifact.data)
