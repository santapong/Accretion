from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from accretion.contracts import ArtifactRef, WorkspaceLease
from accretion.ids import new_id


class WorkspaceError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, root: Path, artifact_root: Path) -> None:
        self.root = root.resolve()
        self.artifact_root = artifact_root.resolve()

    async def acquire(
        self, *, project_id: str, run_id: str, repository: Path, base_revision: str = "HEAD"
    ) -> WorkspaceLease:
        repository = repository.resolve(strict=True)
        await self._git(repository, "rev-parse", "--is-inside-work-tree")
        revision = (await self._git(repository, "rev-parse", base_revision)).strip()
        self.root.mkdir(parents=True, exist_ok=True)
        path = (self.root / run_id).resolve()
        if path.exists():
            raise WorkspaceError(f"workspace path already exists for {run_id}")
        branch = f"accretion/run/{run_id}"
        await self._git(repository, "worktree", "add", "-b", branch, str(path), revision)
        return WorkspaceLease(
            lease_id=new_id("workspace"),
            project_id=project_id,
            run_id=run_id,
            base_revision=revision,
            path=path,
            branch_name=branch,
        )

    async def inspect(self, lease: WorkspaceLease) -> str:
        if not lease.path.exists():
            return "MISSING"
        try:
            revision = (await self._git(lease.path, "rev-parse", "HEAD")).strip()
        except WorkspaceError:
            return "INVALID"
        return "CONSISTENT" if revision.startswith(lease.base_revision) else "REVISION_CONFLICT"

    async def diff(self, lease: WorkspaceLease) -> str:
        """Return the current worktree delta without mutating the workspace."""

        tracked = await self._git(
            lease.path,
            "diff",
            "--no-ext-diff",
            "--binary",
            "HEAD",
        )
        untracked_output = await self._git(
            lease.path,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        parts = [tracked]
        for relative_path in (item for item in untracked_output.split("\0") if item):
            parts.append(await self._untracked_diff(lease.path, relative_path))
        return "".join(parts)

    async def capture_diff(
        self,
        lease: WorkspaceLease,
        *,
        name: str = "workspace.patch",
        kind: str = "GIT_DIFF",
    ) -> ArtifactRef | None:
        if Path(name).name != name or not name.endswith(".patch"):
            raise WorkspaceError("artifact name must be a local .patch filename")
        diff = await self.diff(lease)
        if not diff:
            return None
        target_dir = self.artifact_root / lease.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if target.exists():
            raise WorkspaceError(f"artifact already exists: {target.name}")
        target.write_text(diff, encoding="utf-8")
        digest = hashlib.sha256(diff.encode()).hexdigest()
        return ArtifactRef(
            artifact_id=new_id("artifact"),
            run_id=lease.run_id,
            kind=kind,
            path=target,
            sha256=digest,
        )

    async def release(self, lease: WorkspaceLease, *, successful: bool) -> None:
        if not successful or not lease.path.exists():
            return
        common_dir = Path(
            (
                await self._git(
                    lease.path, "rev-parse", "--path-format=absolute", "--git-common-dir"
                )
            ).strip()
        )
        repository = common_dir.parent
        await self._git(repository, "worktree", "remove", "--force", str(lease.path))

    @staticmethod
    async def _git(cwd: Path, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip()
            raise WorkspaceError(message or f"git {' '.join(args)} failed")
        return stdout.decode(errors="replace")

    @staticmethod
    async def _untracked_diff(cwd: Path, relative_path: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--no-ext-diff",
            "--binary",
            "--no-index",
            "--",
            "/dev/null",
            relative_path,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode not in {0, 1}:
            message = stderr.decode(errors="replace").strip()
            raise WorkspaceError(message or f"git could not diff {relative_path}")
        return stdout.decode(errors="replace")
