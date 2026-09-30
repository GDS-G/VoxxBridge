from __future__ import annotations

from voxbridge.audio_store import AudioArtifactStore


def test_store_returns_artifact_until_expiry():
    now = [1_000.0]
    store = AudioArtifactStore(
        ttl_seconds=60,
        max_items=4,
        max_bytes=1_024,
        clock=lambda: now[0],
    )

    token, created = store.put(
        b"audio",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="voxbridge-test.mp3",
    )

    assert created.expires_at == 1_060.0
    assert store.get(token) == created
    assert store.item_count == 1
    assert store.total_bytes == 5

    now[0] = 1_060.0
    assert store.get(token) is None
    assert store.item_count == 0
    assert store.total_bytes == 0


def test_store_evicts_oldest_to_item_and_byte_limits():
    now = [1_000.0]
    store = AudioArtifactStore(
        ttl_seconds=60,
        max_items=2,
        max_bytes=7,
        clock=lambda: now[0],
    )

    first, _ = store.put(
        b"111",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="first.mp3",
    )
    now[0] += 1
    second, _ = store.put(
        b"222",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="second.mp3",
    )
    assert store.get(first) is not None

    now[0] += 1
    third, _ = store.put(
        b"3333",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="third.mp3",
    )

    assert store.get(second) is None
    assert store.get(first) is not None
    assert store.get(third) is not None
    assert store.item_count == 2
    assert store.total_bytes == 7


def test_store_rejects_empty_or_individually_oversized_artifacts():
    store = AudioArtifactStore(ttl_seconds=60, max_items=2, max_bytes=4)

    for data in (b"", b"12345"):
        try:
            store.put(
                data,
                mime_type="audio/mpeg",
                format_id="mp3",
                file_name="test.mp3",
            )
        except ValueError:
            pass
        else:  # pragma: no cover - assertion helper without pytest dependency
            raise AssertionError("expected ValueError")

    assert store.item_count == 0
    assert store.total_bytes == 0


def test_store_tokens_are_opaque_and_unique():
    store = AudioArtifactStore(ttl_seconds=60, max_items=4, max_bytes=1_024)

    first, _ = store.put(
        b"one",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="same.mp3",
    )
    second, second_artifact = store.put(
        b"two",
        mime_type="audio/mpeg",
        format_id="mp3",
        file_name="same.mp3",
    )

    assert first != second
    assert "same" not in first
    assert "same" not in second
    assert len(first) >= 32
    assert len(second) >= 32
    assert store.get_by_file_name("same.mp3") == second_artifact
    assert store.get_by_file_name("missing.mp3") is None
