from __future__ import annotations

import fcntl
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from accretion.contracts import EvidenceClass
from accretion.contracts.robotics.values import ContentAddressedArtifactRef
from accretion.robotics.artifacts import MAX_STREAM_CHUNKS, ArtifactStore
from accretion.robotics.errors import RoboticsError, RoboticsErrorCode


def put(store: ArtifactStore, data: bytes) -> ContentAddressedArtifactRef:
    return store.put(
        data,
        media_type="application/octet-stream",
        retention_class="RESEARCH_ARCHIVE",
        evidence_class=EvidenceClass.SIMULATION,
    )


def test_roundtrip_retains_exact_bytes_and_metadata_without_mutating_existing_blob(
    tmp_path: Path,
) -> None:
    with ArtifactStore(tmp_path) as store:
        data = b"\x00\x01\xffpayload\n" * 100
        ref = put(store, data)
        before = (tmp_path / (ref.digest + ".blob")).stat()
        assert ref.digest == hashlib.sha256(data).hexdigest()
        assert ref.retention_class == "RESEARCH_ARCHIVE"
        assert put(store, data) == ref
        after = (tmp_path / (ref.digest + ".blob")).stat()
        assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
        assert after.st_mode & 0o777 == 0o400
        assert store.read_bytes(ref, max_bytes=len(data)) == data
        assert all(
            len(chunk) <= 7 for chunk in store.iter_bytes(ref, max_bytes=len(data), chunk_size=7)
        )
        assert store.read_bytes(put(store, b""), max_bytes=1) == b""


@pytest.mark.parametrize("mutation", ["digest", "length", "symlink", "fifo", "uri", "missing"])
def test_tampered_or_substituted_artifacts_fail_closed(tmp_path: Path, mutation: str) -> None:
    with ArtifactStore(tmp_path) as store:
        ref = put(store, b"original")
        file = tmp_path / (ref.digest + ".blob")
        if mutation == "digest":
            file.chmod(0o600)
            file.write_bytes(b"tampered")
        elif mutation == "length":
            ref.size_bytes += 1
        elif mutation == "symlink":
            file.unlink()
            outside = tmp_path / "outside"
            outside.write_bytes(b"original")
            file.symlink_to(outside)
        elif mutation == "fifo":
            file.unlink()
            os.mkfifo(file)
        elif mutation == "uri":
            ref.uri = "file:///etc/passwd"
        else:
            file.unlink()
        with pytest.raises(RoboticsError):
            store.read_bytes(ref, max_bytes=100)


def test_stream_detects_changes_after_open_and_never_accepts_incomplete_read(
    tmp_path: Path,
) -> None:
    with ArtifactStore(tmp_path) as store:
        ref = put(store, b"abcdefgh")
        chunks = store.iter_bytes(ref, max_bytes=8, chunk_size=4)
        assert next(chunks) == b"abcd"
        file = tmp_path / (ref.digest + ".blob")
        file.chmod(0o600)
        file.write_bytes(b"abcdefgh")  # Same bytes, but mutation invalidates this read attempt.
        with pytest.raises(RoboticsError, match="integrity"):
            list(chunks)


def test_failed_write_does_not_publish_or_leave_partial_artifacts(tmp_path: Path) -> None:
    def failing_source():
        yield b"part"
        raise RuntimeError("injected bounded source failure")

    with ArtifactStore(tmp_path) as store:
        with pytest.raises(RuntimeError, match="injected"):
            store.put_stream(
                failing_source(),
                media_type="application/octet-stream",
                retention_class="RUN",
                evidence_class=EvidenceClass.SIMULATION,
            )
        assert not list(tmp_path.glob("*.blob"))
        assert not list(tmp_path.glob(".pending-*"))


def test_byte_quota_and_concurrent_publish_are_serialized(tmp_path: Path) -> None:
    with ArtifactStore(tmp_path, max_artifact_bytes=8, max_store_bytes=12) as store:

        def attempt(data: bytes):
            try:
                return put(store, data)
            except RoboticsError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [b"12345678", b"abcdefgh"]))
        assert sum(isinstance(value, ContentAddressedArtifactRef) for value in results) == 1
        assert RoboticsErrorCode.RESOURCE_CAP_EXHAUSTED in results
        assert sum(file.stat().st_size for file in tmp_path.glob("*.blob")) == 8
        with pytest.raises(RoboticsError) as too_large:
            put(store, b"012345678")
        assert too_large.value.code is RoboticsErrorCode.PAYLOAD_TOO_LARGE
        assert not list(tmp_path.glob(".pending-*"))


def test_parallel_same_content_has_one_immutable_object(tmp_path: Path) -> None:
    with ArtifactStore(tmp_path) as first, ArtifactStore(tmp_path) as second:
        with ThreadPoolExecutor(max_workers=2) as pool:
            refs = list(pool.map(lambda store: put(store, b"shared"), [first, second]))
        assert refs[0] == refs[1]
        assert len(list(tmp_path.glob("*.blob"))) == 1


def test_limits_are_checked_before_read_and_infinite_empty_chunks_are_bounded(
    tmp_path: Path,
) -> None:
    with ArtifactStore(tmp_path) as store:
        ref = put(store, b"bounded")
        for bound in [0, -1, True, 6]:
            with pytest.raises(RoboticsError):
                store.read_bytes(ref, max_bytes=bound)
        with pytest.raises(RoboticsError) as count_limit:
            store.put_stream(
                (b"" for _ in range(MAX_STREAM_CHUNKS + 1)),
                media_type="application/octet-stream",
                retention_class="RUN",
                evidence_class=EvidenceClass.SIMULATION,
            )
        assert count_limit.value.code is RoboticsErrorCode.RESOURCE_CAP_EXHAUSTED
        assert not list(tmp_path.glob(".pending-*"))


def test_physical_metadata_and_symlink_roots_are_refused(tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RoboticsError):
        ArtifactStore(alias)
    with ArtifactStore(tmp_path / "store") as store:
        with pytest.raises(RoboticsError):
            store.put(
                b"simulation",
                media_type="application/octet-stream",
                retention_class="RUN",
                evidence_class=EvidenceClass.PHYSICAL,
            )
    assert not list((tmp_path / "store").iterdir())


def test_writer_lock_is_bounded_and_closed_store_cannot_be_reused(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, lock_timeout_seconds=0.01)
    with (tmp_path / ".publish.lock").open("wb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(RoboticsError) as busy:
            put(store, b"hello")
        assert busy.value.code is RoboticsErrorCode.RESOURCE_CAP_EXHAUSTED
    store.close()
    store.close()
    with pytest.raises(RoboticsError):
        put(store, b"hello")
