from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    truncated: bool = False
    startup_error: str | None = None


async def run_bounded_process(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
    max_output_bytes: int,
) -> BoundedProcessResult:
    """Execute trusted argv directly while bounding time and captured output."""
    if not argv or any(not item or "\0" in item for item in argv):
        raise ValueError("argv must contain non-empty, NUL-free arguments")
    if timeout_seconds <= 0 or max_output_bytes <= 0:
        raise ValueError("process bounds must be positive")

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return BoundedProcessResult(
            returncode=None,
            stdout=b"",
            stderr=b"",
            startup_error=f"{type(exc).__name__}: {exc}",
        )

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    remaining = max_output_bytes
    truncated = False

    async def read_stream(
        stream: asyncio.StreamReader | None, destination: list[bytes]
    ) -> None:
        nonlocal remaining, truncated
        if stream is None:
            return
        while chunk := await stream.read(64 * 1024):
            captured_length = 0
            if remaining > 0:
                captured = chunk[:remaining]
                destination.append(captured)
                captured_length = len(captured)
                remaining -= captured_length
            if len(chunk) > captured_length:
                truncated = True

    stdout_task = asyncio.create_task(read_stream(process.stdout, stdout_chunks))
    stderr_task = asyncio.create_task(read_stream(process.stderr, stderr_chunks))
    try:
        async with asyncio.timeout(timeout_seconds):
            await process.wait()
            await asyncio.gather(stdout_task, stderr_task)
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
        raise
    except TimeoutError:
        process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
        return BoundedProcessResult(
            returncode=process.returncode,
            stdout=b"".join(stdout_chunks),
            stderr=b"".join(stderr_chunks),
            timed_out=True,
            truncated=truncated,
        )

    return BoundedProcessResult(
        returncode=process.returncode,
        stdout=b"".join(stdout_chunks),
        stderr=b"".join(stderr_chunks),
        truncated=truncated,
    )
