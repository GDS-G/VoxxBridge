from __future__ import annotations

import io
import wave
from dataclasses import dataclass


class AudioCompositionError(ValueError):
    """Raised when generated audio segments cannot be joined safely."""


@dataclass(frozen=True, slots=True)
class WavSegment:
    audio: bytes
    pause_after_ms: int = 250


@dataclass(frozen=True, slots=True)
class PcmWav:
    channels: int
    sample_width: int
    frame_rate: int
    frames: bytes


def read_pcm_wav(audio: bytes, *, segment_number: int) -> PcmWav:
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getcomptype() != "NONE":
                raise AudioCompositionError(
                    f"segment {segment_number} uses compressed WAV audio, which cannot be joined"
                )
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            frame_rate = source.getframerate()
            frame_count = source.getnframes()
            frames = source.readframes(frame_count)
    except AudioCompositionError:
        raise
    except (EOFError, wave.Error) as exc:
        raise AudioCompositionError(f"segment {segment_number} is not valid PCM WAV audio") from exc

    if channels < 1 or sample_width < 1 or frame_rate < 1 or not frames:
        raise AudioCompositionError(f"segment {segment_number} contains no playable WAV audio")
    expected_size = frame_count * channels * sample_width
    if len(frames) != expected_size:
        raise AudioCompositionError(f"segment {segment_number} has an incomplete WAV payload")
    return PcmWav(
        channels=channels,
        sample_width=sample_width,
        frame_rate=frame_rate,
        frames=frames,
    )


class PcmWavComposer:
    """Incrementally join compatible PCM WAV streams with strict output caps."""

    def __init__(self, *, max_pcm_bytes: int, max_duration_seconds: float) -> None:
        self._max_pcm_bytes = max_pcm_bytes
        self._max_duration_seconds = max_duration_seconds
        self._output = io.BytesIO()
        self._destination: wave.Wave_write | None = None
        self._parameters: tuple[int, int, int] | None = None
        self._frames_written = 0
        self._finished = False

    @property
    def parameters(self) -> tuple[int, int, int] | None:
        return self._parameters

    @property
    def duration_seconds(self) -> float:
        if self._parameters is None:
            return 0.0
        return self._frames_written / self._parameters[2]

    def append(self, segment: WavSegment, *, segment_number: int) -> None:
        if self._finished:
            raise AudioCompositionError("the WAV composer is already finished")
        if segment.pause_after_ms < 0:
            raise AudioCompositionError("pause duration cannot be negative")

        wav = read_pcm_wav(segment.audio, segment_number=segment_number)
        parameters = (wav.channels, wav.sample_width, wav.frame_rate)
        if self._parameters is None:
            self._parameters = parameters
            self._destination = wave.open(self._output, "wb")  # noqa: SIM115
            self._destination.setnchannels(wav.channels)
            self._destination.setsampwidth(wav.sample_width)
            self._destination.setframerate(wav.frame_rate)
        elif parameters != self._parameters:
            raise AudioCompositionError(
                f"segment {segment_number} has different WAV channel, sample-width, "
                "or sample-rate settings"
            )

        pause_frames = round(wav.frame_rate * segment.pause_after_ms / 1000)
        source_frames = len(wav.frames) // (wav.channels * wav.sample_width)
        projected_frames = self._frames_written + source_frames + pause_frames
        projected_pcm_bytes = projected_frames * wav.channels * wav.sample_width
        projected_duration = projected_frames / wav.frame_rate
        if projected_pcm_bytes > self._max_pcm_bytes:
            raise AudioCompositionError("combined PCM audio exceeds the configured byte limit")
        if projected_duration > self._max_duration_seconds:
            raise AudioCompositionError("combined audio exceeds the configured duration limit")

        assert self._destination is not None
        self._destination.writeframesraw(wav.frames)
        self._frames_written += source_frames
        self._write_silence(pause_frames, wav.channels, wav.sample_width)

    def _write_silence(self, frames: int, channels: int, sample_width: int) -> None:
        if frames <= 0:
            return
        assert self._destination is not None
        silent_sample = b"\x80" if sample_width == 1 else b"\x00" * sample_width
        silent_frame = silent_sample * channels
        remaining = frames
        while remaining:
            chunk_frames = min(remaining, 8_192)
            self._destination.writeframesraw(silent_frame * chunk_frames)
            self._frames_written += chunk_frames
            remaining -= chunk_frames

    def finish(self) -> bytes:
        if self._finished:
            raise AudioCompositionError("the WAV composer is already finished")
        if self._destination is None or self._parameters is None:
            raise AudioCompositionError("at least one WAV segment is required")
        self._destination.close()
        self._destination = None
        self._finished = True
        return self._output.getvalue()

    def close(self) -> None:
        if self._destination is not None:
            self._destination.close()
            self._destination = None
        self._finished = True


def compose_pcm_wav(
    segments: list[WavSegment],
    *,
    max_pcm_bytes: int = 100 * 1024 * 1024,
    max_duration_seconds: float = 3_600,
) -> bytes:
    """Convenience wrapper used by deterministic unit tests and small callers."""

    composer = PcmWavComposer(
        max_pcm_bytes=max_pcm_bytes,
        max_duration_seconds=max_duration_seconds,
    )
    try:
        for index, segment in enumerate(segments, start=1):
            composer.append(segment, segment_number=index)
        return composer.finish()
    except Exception:
        composer.close()
        raise
