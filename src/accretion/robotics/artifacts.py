"""Bounded immutable blobs, with no scope or execution authority.

Services must authorize the reference's project before calling this primitive.
Readers must exhaust iter_bytes and validate its result before using evidence:
the final length/digest check can fail after earlier chunks have been yielded.
No deletion or retention downgrade is exposed; lifecycle policy belongs to the
registry/recorder. Files and locks are opened relative to an anchored directory
descriptor, never a caller-supplied artifact URI path.
"""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import re
import stat
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import ValidationError

from accretion.contracts import EvidenceClass
from accretion.contracts.robotics.values import ContentAddressedArtifactRef
from accretion.robotics.errors import RoboticsError, RoboticsErrorCode

_BLOB_NAME = re.compile(r"[0-9a-f]{64}\.blob\Z")
MAX_CHUNK_BYTES = 1024 * 1024
MAX_STREAM_CHUNKS = 16_384


def _positive_int(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise RoboticsError(RoboticsErrorCode.INVALID_REQUEST)
    return value


class ArtifactStore:
    """Process-safe publish-once store; close after all readers/writers stop."""

    def __init__(
        self,
        root: Path,
        *,
        max_artifact_bytes: int = 64 * 1024 * 1024,
        max_store_bytes: int = 1024 * 1024 * 1024,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        self.max_artifact_bytes = _positive_int(max_artifact_bytes)
        self.max_store_bytes = _positive_int(max_store_bytes)
        if (
            isinstance(lock_timeout_seconds, bool)
            or not math.isfinite(lock_timeout_seconds)
            or lock_timeout_seconds <= 0
        ):
            raise RoboticsError(RoboticsErrorCode.INVALID_REQUEST)
        self.lock_timeout_seconds = lock_timeout_seconds
        self._fd = -1
        root = root.absolute()
        try:
            # Root is trusted deployment configuration; refuse accidental aliases.
            if any(part.is_symlink() for part in (root, *root.parents)):
                raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID)
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise RoboticsError(RoboticsErrorCode.ARTIFACT_UNAVAILABLE) from exc

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _directory(self) -> int:
        if self._fd < 0:
            raise RoboticsError(RoboticsErrorCode.ARTIFACT_UNAVAILABLE)
        return self._fd

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        # Each operation opens its own file description: sharing one lock fd
        # would not serialize threads using this same ArtifactStore instance.
        lock = os.open(
            ".publish.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=self._directory(),
        )
        try:
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID)
            deadline = time.monotonic() + self.lock_timeout_seconds
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RoboticsError(RoboticsErrorCode.RESOURCE_CAP_EXHAUSTED) from None
                    time.sleep(0.005)
            yield
        finally:
            os.close(lock)

    def _used_bytes(self) -> int:
        total = 0
        with os.scandir(self._directory()) as entries:
            for entry in entries:
                if entry.name == ".publish.lock":
                    continue
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or not (
                    _BLOB_NAME.fullmatch(entry.name) or entry.name.startswith(".pending-")
                ):
                    raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID)
                # Interrupted writes consume quota until explicit recovery; do
                # not silently unlink a file another reader could be inspecting.
                total += info.st_size
        return total

    def put(
        self,
        data: bytes,
        *,
        media_type: str,
        retention_class: Literal["RUN", "PROJECT", "RESEARCH_ARCHIVE"],
        evidence_class: EvidenceClass,
    ) -> ContentAddressedArtifactRef:
        if not isinstance(data, bytes):
            raise RoboticsError(RoboticsErrorCode.INVALID_REQUEST)
        if len(data) > self.max_artifact_bytes:
            raise RoboticsError(RoboticsErrorCode.PAYLOAD_TOO_LARGE)
        return self.put_stream(
            (data[i : i + MAX_CHUNK_BYTES] for i in range(0, len(data), MAX_CHUNK_BYTES)),
            media_type=media_type,
            retention_class=retention_class,
            evidence_class=evidence_class,
        )

    def put_stream(
        self,
        chunks: Iterable[bytes],
        *,
        media_type: str,
        retention_class: Literal["RUN", "PROJECT", "RESEARCH_ARCHIVE"],
        evidence_class: EvidenceClass,
    ) -> ContentAddressedArtifactRef:
        # Validate metadata before touching storage, including PHYSICAL refusal.
        try:
            template = ContentAddressedArtifactRef(
                uri="artifact://sha256/" + "0" * 64,
                digest="0" * 64,
                media_type=media_type,
                size_bytes=0,
                retention_class=retention_class,
                evidence_class=evidence_class,
            )
        except ValidationError as exc:
            raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID) from exc
        temporary = ".pending-" + uuid4().hex
        created = False
        try:
            with self._writer_lock():
                used = self._used_bytes()
                fd = os.open(
                    temporary,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._directory(),
                )
                created = True
                digest = hashlib.sha256()
                size = 0
                try:
                    for number, chunk in enumerate(chunks, 1):
                        if number > MAX_STREAM_CHUNKS:
                            raise RoboticsError(RoboticsErrorCode.RESOURCE_CAP_EXHAUSTED)
                        if not isinstance(chunk, bytes):
                            raise RoboticsError(RoboticsErrorCode.INVALID_REQUEST)
                        if (
                            len(chunk) > MAX_CHUNK_BYTES
                            or size + len(chunk) > self.max_artifact_bytes
                        ):
                            raise RoboticsError(RoboticsErrorCode.PAYLOAD_TOO_LARGE)
                        if used + size + len(chunk) > self.max_store_bytes:
                            raise RoboticsError(RoboticsErrorCode.RESOURCE_CAP_EXHAUSTED)
                        view = memoryview(chunk)
                        while view:
                            written = os.write(fd, view)
                            if written <= 0:
                                raise RoboticsError(RoboticsErrorCode.ARTIFACT_UNAVAILABLE)
                            view = view[written:]
                        digest.update(chunk)
                        size += len(chunk)
                    os.fchmod(fd, 0o400)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                value = digest.hexdigest()
                reference = ContentAddressedArtifactRef.model_validate(
                    {
                        **template.model_dump(),
                        "digest": value,
                        "uri": "artifact://sha256/" + value,
                        "size_bytes": size,
                    }
                )
                try:
                    os.link(
                        temporary,
                        value + ".blob",
                        src_dir_fd=self._directory(),
                        dst_dir_fd=self._directory(),
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    # Reuse is permitted only if the existing immutable object
                    # has the bytes its name claims. Never overwrite corruption.
                    for _ in self.iter_bytes(
                        reference,
                        max_bytes=self.max_artifact_bytes,
                        chunk_size=64 * 1024,
                    ):
                        pass
                os.unlink(temporary, dir_fd=self._directory())
                created = False
                os.fsync(self._directory())
                return reference
        except OSError as exc:
            raise RoboticsError(RoboticsErrorCode.ARTIFACT_UNAVAILABLE) from exc
        finally:
            if created:
                try:
                    os.unlink(temporary, dir_fd=self._directory())
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise RoboticsError(RoboticsErrorCode.ARTIFACT_UNAVAILABLE) from exc

    def iter_bytes(
        self,
        ref: ContentAddressedArtifactRef,
        *,
        max_bytes: int,
        chunk_size: int,
    ) -> Iterator[bytes]:
        _positive_int(max_bytes)
        _positive_int(chunk_size)
        if chunk_size > MAX_CHUNK_BYTES:
            raise RoboticsError(RoboticsErrorCode.PAYLOAD_TOO_LARGE)
        try:
            checked = ContentAddressedArtifactRef.model_validate(ref.model_dump(mode="python"))
        except (ValidationError, AttributeError) as exc:
            raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID) from exc
        if checked.size_bytes > min(max_bytes, self.max_artifact_bytes):
            raise RoboticsError(RoboticsErrorCode.PAYLOAD_TOO_LARGE)
        try:
            fd = os.open(
                checked.digest + ".blob",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=self._directory(),
            )
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_size != checked.size_bytes:
                    raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID)
                digest = hashlib.sha256()
                size = 0
                while True:
                    # Read at most one extra byte to detect growth; never follow
                    # an unbounded changed file or allocate above the read cap.
                    chunk = os.read(fd, min(chunk_size, checked.size_bytes - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > checked.size_bytes:
                        raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID)
                    digest.update(chunk)
                    yield chunk
                after = os.fstat(fd)
                if (
                    size != checked.size_bytes
                    or digest.hexdigest() != checked.digest
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                ):
                    raise RoboticsError(RoboticsErrorCode.ARTIFACT_INVALID)
            finally:
                os.close(fd)
        except OSError as exc:
            raise RoboticsError(RoboticsErrorCode.ARTIFACT_UNAVAILABLE) from exc

    def read_bytes(self, ref: ContentAddressedArtifactRef, *, max_bytes: int) -> bytes:
        """Return only completely verified bytes; allocation is bounded by max_bytes."""
        return b"".join(self.iter_bytes(ref, max_bytes=max_bytes, chunk_size=64 * 1024))
