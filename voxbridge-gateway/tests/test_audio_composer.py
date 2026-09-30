from __future__ import annotations

import io
import struct
import wave

import pytest

from voxbridge.audio_composer import (
    AudioCompositionError,
    PcmWavComposer,
    WavSegment,
    compose_pcm_wav,
    read_pcm_wav,
)


def _wav(
    samples: list[int],
    *,
    channels: int = 1,
    sample_width: int = 2,
    frame_rate: int = 1_000,
) -> bytes:
    if sample_width == 1:
        frames = bytes(samples)
    elif sample_width == 2:
        frames = struct.pack(f"<{len(samples)}h", *samples)
    else:
        raise AssertionError("test helper only supports 8-bit and 16-bit samples")
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(sample_width)
        destination.setframerate(frame_rate)
        destination.writeframes(frames)
    return output.getvalue()


def _read_samples(audio: bytes) -> tuple[wave._wave_params, list[int]]:
    with wave.open(io.BytesIO(audio), "rb") as source:
        parameters = source.getparams()
        frames = source.readframes(source.getnframes())
    if parameters.sampwidth == 1:
        samples = list(frames)
    else:
        samples = list(struct.unpack(f"<{len(frames) // 2}h", frames))
    return parameters, samples


def test_compose_pcm_wav_preserves_segment_order_and_inserts_silence() -> None:
    combined = compose_pcm_wav(
        [
            WavSegment(_wav([100, 200]), pause_after_ms=2),
            WavSegment(_wav([-300, -400]), pause_after_ms=0),
        ]
    )

    parameters, samples = _read_samples(combined)

    assert parameters.nchannels == 1
    assert parameters.sampwidth == 2
    assert parameters.framerate == 1_000
    assert parameters.nframes == 6
    assert samples == [100, 200, 0, 0, -300, -400]


def test_compose_pcm_wav_uses_unsigned_midpoint_for_8_bit_silence() -> None:
    combined = compose_pcm_wav(
        [
            WavSegment(_wav([0, 255], sample_width=1), pause_after_ms=1),
            WavSegment(_wav([17], sample_width=1), pause_after_ms=0),
        ]
    )

    _, samples = _read_samples(combined)

    assert samples == [0, 255, 128, 17]


@pytest.mark.parametrize(
    "second",
    [
        _wav([1, 2], channels=2),
        _wav([1], sample_width=1),
        _wav([1], frame_rate=2_000),
    ],
    ids=["channels", "sample-width", "frame-rate"],
)
def test_compose_pcm_wav_rejects_incompatible_stream_parameters(second: bytes) -> None:
    with pytest.raises(AudioCompositionError, match="segment 2 has different WAV"):
        compose_pcm_wav(
            [
                WavSegment(_wav([1]), pause_after_ms=0),
                WavSegment(second, pause_after_ms=0),
            ]
        )


@pytest.mark.parametrize(
    ("audio", "message"),
    [
        (b"not-a-wave", "not valid PCM WAV"),
        (_wav([]), "contains no playable WAV audio"),
    ],
)
def test_read_pcm_wav_rejects_invalid_or_empty_audio(audio: bytes, message: str) -> None:
    with pytest.raises(AudioCompositionError, match=message):
        read_pcm_wav(audio, segment_number=3)


def test_incremental_composer_enforces_byte_and_duration_caps() -> None:
    byte_limited = PcmWavComposer(max_pcm_bytes=5, max_duration_seconds=10)
    with pytest.raises(AudioCompositionError, match="byte limit"):
        byte_limited.append(WavSegment(_wav([1, 2, 3]), 0), segment_number=1)
    byte_limited.close()

    duration_limited = PcmWavComposer(max_pcm_bytes=100, max_duration_seconds=0.002)
    with pytest.raises(AudioCompositionError, match="duration limit"):
        duration_limited.append(WavSegment(_wav([1, 2, 3]), 0), segment_number=1)
    duration_limited.close()


def test_composer_cannot_be_finished_twice_or_appended_after_finish() -> None:
    composer = PcmWavComposer(max_pcm_bytes=100, max_duration_seconds=1)
    composer.append(WavSegment(_wav([1]), 0), segment_number=1)
    assert _read_samples(composer.finish())[1] == [1]

    with pytest.raises(AudioCompositionError, match="already finished"):
        composer.finish()
    with pytest.raises(AudioCompositionError, match="already finished"):
        composer.append(WavSegment(_wav([2]), 0), segment_number=2)
