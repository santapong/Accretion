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

    async def capture_diff(self, lease: WorkspaceLease) -> ArtifactRef | None:
        diff = await self._git(lease.path, "diff", "--binary", "HEAD")
        if not diff:
            return None
        target_dir = self.artifact_root / lease.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "workspace.patch"
        target.write_text(diff, encoding="utf-8")
        digest = hashlib.sha256(diff.encode()).hexdigest()
        return ArtifactRef(
            artifact_id=new_id("artifact"),
            run_id=lease.run_id,
            kind="GIT_DIFF",
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
