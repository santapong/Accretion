"""Real Unix artifact IPC and scoped storage witnesses; no robot/host activation."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

import pytest

from accretion.contracts import EvidenceClass
from accretion.contracts.canonical import canonical_json
from accretion.contracts.robotics.values import SimulationArtifactRef
from accretion.robotics.artifacts import ArtifactStore
from accretion.robotics.errors import RoboticsError
from accretion.robotics.errors import RoboticsErrorCode as Code
from accretion.robotics.host.artifact_channel import (
    ArtifactCall,
    LeaseArtifactChannel,
    UnixArtifacts,
)


def ref(data: bytes, *, media: str = "application/octet-stream") -> SimulationArtifactRef:
    digest = hashlib.sha256(data).hexdigest()
    return SimulationArtifactRef(
        uri="artifact://sha256/" + digest,
        digest=digest,
        size_bytes=len(data),
        media_type=media,
        retention_class="RUN",
    )


def put(client: UnixArtifacts, data: bytes):
    return client.put(
        data,
        media_type="application/octet-stream",
        retention_class="RUN",
        evidence_class=EvidenceClass.SIMULATION,
    )


def read(client: UnixArtifacts, reference: SimulationArtifactRef) -> bytes:
    return b"".join(client.iter_bytes(reference, max_bytes=1024 * 1024, chunk_size=4096))


async def start(tmp_path: Path, *, output: int = 1024 * 1024, initial=()):
    directory = tmp_path / "ipc"
    directory.mkdir(mode=0o700)
    store = ArtifactStore(tmp_path / "store")
    state = {"active": True, "calls": 0}

    async def access(operation: str, reference: SimulationArtifactRef) -> None:
        state["calls"] += 1
        if not state["active"]:
            raise RoboticsError(Code.LEASE_INVALID)

    channel = LeaseArtifactChannel(
        directory,
        store,
        allowed_reads=initial,
        access=access,
        max_artifact_bytes=1024 * 1024,
        max_output_bytes=output,
        timeout_seconds=0.5,
    )
    await channel.start()
    return channel, UnixArtifacts(channel.path, timeout_seconds=1), store, state


async def finish(channel: LeaseArtifactChannel, store: ArtifactStore) -> None:
    await channel.close()
    store.close()


async def test_publish_then_read_exact_bytes_and_duplicate_reuse_at_quota(tmp_path: Path) -> None:
    data = b"known exact evidence" * 5000
    channel, client, store, _ = await start(tmp_path, output=len(data))
    try:
        reference = await asyncio.to_thread(put, client, data)
        assert reference == ref(data)
        assert await asyncio.to_thread(read, client, reference) == data
        assert await asyncio.to_thread(put, client, data) == reference
        assert channel.charged_bytes == len(data)
        assert channel.inventory() == (reference,)
    finally:
        await finish(channel, store)


async def test_global_store_presence_does_not_grant_foreign_read(tmp_path: Path) -> None:
    channel, client, store, _ = await start(tmp_path)
    try:
        reference = store.put(
            b"foreign project bytes",
            media_type="application/octet-stream",
            retention_class="RUN",
            evidence_class=EvidenceClass.SIMULATION,
        )
        with pytest.raises(RoboticsError) as exc:
            await asyncio.to_thread(read, client, reference)
        assert exc.value.code is Code.ARTIFACT_UNAVAILABLE
        assert not channel.inventory()
    finally:
        await finish(channel, store)


async def test_exact_initial_reference_is_readable_without_upload_credit(tmp_path: Path) -> None:
    data = b"approved model evidence"
    reference = ref(data)
    channel, client, store, _ = await start(tmp_path, initial=[reference])
    try:
        store.put(
            data,
            media_type=reference.media_type,
            retention_class="RUN",
            evidence_class=EvidenceClass.SIMULATION,
        )
        assert await asyncio.to_thread(read, client, reference) == data
        assert channel.charged_bytes == 0
        altered = reference.model_copy(update={"media_type": "text/plain"})
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(read, client, altered)
    finally:
        await finish(channel, store)


async def test_mutating_returned_inventory_cannot_authorize_new_metadata(tmp_path: Path) -> None:
    channel, client, store, _ = await start(tmp_path)
    try:
        reference = await asyncio.to_thread(put, client, b"sealed")
        leaked = channel.inventory()[0]
        leaked.media_type = "text/plain"
        assert channel.inventory()[0] == reference
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(read, client, leaked)
    finally:
        await finish(channel, store)


async def test_revoked_scope_refuses_existing_artifact_and_new_write(tmp_path: Path) -> None:
    channel, client, store, state = await start(tmp_path)
    try:
        reference = await asyncio.to_thread(put, client, b"first")
        state["active"] = False
        for operation, value in [(read, reference), (put, b"second")]:
            with pytest.raises(RoboticsError) as exc:
                await asyncio.to_thread(operation, client, value)
            assert exc.value.code is Code.LEASE_INVALID
        assert channel.charged_bytes == 5
    finally:
        await finish(channel, store)


async def test_corrupt_stored_bytes_never_receive_ready_response(tmp_path: Path) -> None:
    channel, client, store, _ = await start(tmp_path)
    try:
        reference = await asyncio.to_thread(put, client, b"original")
        path = tmp_path / "store" / (reference.digest + ".blob")
        path.chmod(0o600)
        path.write_bytes(b"corrupt!")
        with pytest.raises(RoboticsError) as exc:
            await asyncio.to_thread(read, client, reference)
        assert exc.value.code is Code.ARTIFACT_INVALID
    finally:
        await finish(channel, store)


@pytest.mark.parametrize("body", [b"bad", b"correct-plus-extra", b""])
async def test_incomplete_or_wrong_upload_keeps_charge_and_publishes_nothing(
    tmp_path: Path, body: bytes
) -> None:
    channel, _, store, _ = await start(tmp_path)
    reference = ref(b"correct")
    try:
        reader, writer = await asyncio.open_unix_connection(channel.path)
        writer.write(canonical_json(ArtifactCall(op="WRITE", reference=reference)) + b"\n" + body)
        await writer.drain()
        writer.write_eof()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        assert b"ERROR" in response and b"DONE" not in response
        writer.close()
        await writer.wait_closed()
        assert channel.charged_bytes == reference.size_bytes and not channel.inventory()
        assert not list((tmp_path / "store").glob("*.blob"))
    finally:
        await finish(channel, store)


async def test_output_quota_and_simulation_retention_limits(tmp_path: Path) -> None:
    channel, client, store, _ = await start(tmp_path, output=3)
    try:
        await asyncio.to_thread(put, client, b"one")
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(put, client, b"two")
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(
                client.put,
                b"physical",
                media_type="text/plain",
                retention_class="RUN",
                evidence_class=EvidenceClass.PHYSICAL,
            )
        with pytest.raises(RoboticsError):
            await asyncio.to_thread(
                client.put,
                b"archive",
                media_type="text/plain",
                retention_class="RESEARCH_ARCHIVE",
                evidence_class=EvidenceClass.SIMULATION,
            )
        assert len(channel.inventory()) == 1 and channel.charged_bytes == 3
    finally:
        await finish(channel, store)


async def test_concurrent_same_upload_charges_once(tmp_path: Path) -> None:
    channel, client, store, _ = await start(tmp_path, output=4)
    try:
        results = await asyncio.gather(*(asyncio.to_thread(put, client, b"same") for _ in range(2)))
        assert results[0] == results[1] and channel.charged_bytes == 4
    finally:
        await finish(channel, store)


async def test_slow_body_deadline_closes_without_publishing(tmp_path: Path) -> None:
    channel, _, store, _ = await start(tmp_path)
    try:
        reader, writer = await asyncio.open_unix_connection(channel.path)
        writer.write(canonical_json(ArtifactCall(op="WRITE", reference=ref(b"expected"))) + b"\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        assert b"ERROR" in response
        writer.close()
        await writer.wait_closed()
        assert channel.charged_bytes == 8 and not channel.inventory()
    finally:
        await finish(channel, store)


async def test_close_drains_publication_after_lost_reply(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    class DelayedStore(ArtifactStore):
        def put(self, data, **kwargs):
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("test publication was not released")
            return super().put(data, **kwargs)

    directory = tmp_path / "ipc"
    directory.mkdir(mode=0o700)
    store = DelayedStore(tmp_path / "store")

    async def access(operation, reference):
        return None

    channel = LeaseArtifactChannel(directory, store, allowed_reads=(), access=access)
    await channel.start()
    client = UnixArtifacts(channel.path, timeout_seconds=2)
    upload = asyncio.create_task(asyncio.to_thread(put, client, b"retained uncertain write"))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        closing = asyncio.create_task(channel.close())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(closing), timeout=0.05)
        assert not closing.done()
        release.set()
        await asyncio.wait_for(closing, timeout=2)
        with pytest.raises(RoboticsError):
            await upload
        assert channel.charged_bytes == 24
        assert not channel.inventory()
        assert store.read_bytes(ref(b"retained uncertain write"), max_bytes=1024) == (
            b"retained uncertain write"
        )
    finally:
        release.set()
        await asyncio.gather(upload, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await channel.close()
        store.close()


async def test_empty_artifact_has_valid_hash_and_complete_framing(tmp_path: Path) -> None:
    channel, client, store, _ = await start(tmp_path)
    try:
        reference = await asyncio.to_thread(put, client, b"")
        assert reference == ref(b"") and await asyncio.to_thread(read, client, reference) == b""
    finally:
        await finish(channel, store)
