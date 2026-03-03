from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from git import Repo
from sqlalchemy import func

from .ast_graph import find_python_files, parse_repo
from .config import settings
from .db import SessionLocal
from .graph_store import graph_store
from .models import Project, ProjectVersion, SourceType, VersionStatus


def ensure_local_path_allowed(local_path: str) -> Path:
    candidate = Path(local_path).resolve()
    root = settings.allowed_local_root
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"Local path does not exist or is not a directory: {candidate}")
    if root not in [candidate, *candidate.parents]:
        raise ValueError(f"Path {candidate} is outside ALLOWED_LOCAL_ROOT={root}")
    return candidate


def detect_default_branch(repo_url: str) -> str:
    import subprocess

    try:
        output = subprocess.check_output(
            ["git", "ls-remote", "--symref", repo_url, "HEAD"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        for line in output.splitlines():
            if line.startswith("ref:") and "\tHEAD" in line:
                ref = line.split()[1]
                return ref.split("/")[-1]
    except Exception:
        pass
    return "main"


def _python_files(repo_path: Path) -> list[Path]:
    return find_python_files(repo_path, ignored_dirnames=settings.ast_excluded_dirnames)


def compute_fingerprint(repo_path: Path) -> str:
    h = hashlib.sha256()
    files = _python_files(repo_path)
    for file_path in files:
        stat = file_path.stat()
        h.update(str(file_path.relative_to(repo_path)).encode("utf-8"))
        h.update(str(int(stat.st_mtime)).encode("utf-8"))
        h.update(str(stat.st_size).encode("utf-8"))
    return h.hexdigest()


def _git_repo_dir(project: Project) -> Path:
    if project.cloned_path:
        return Path(project.cloned_path)
    return settings.repos_storage_root / project.group_slug


def prepare_git_repo(project: Project, sync_remote: bool = True) -> tuple[Path, str | None]:
    if not project.repo_url:
        raise ValueError("Missing repo_url")

    target = _git_repo_dir(project)
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        repo = Repo.clone_from(project.repo_url, target)
    else:
        repo = Repo(target)

    if sync_remote:
        repo.remotes.origin.fetch()

    branch = project.default_branch or "main"
    remote_ref = f"origin/{branch}"
    repo.git.checkout("-B", branch, remote_ref)
    repo.git.reset("--hard", remote_ref)

    return target, repo.head.commit.hexsha


def resolve_source_path(project: Project, sync_remote: bool = True) -> tuple[Path, str | None]:
    if project.source_type == SourceType.LOCAL:
        if not project.local_path:
            raise ValueError("Local project has empty path")
        return ensure_local_path_allowed(project.local_path), None
    return prepare_git_repo(project, sync_remote=sync_remote)


def _next_version(project_id: UUID) -> int:
    db = SessionLocal()
    try:
        current = db.query(func.max(ProjectVersion.version)).filter(ProjectVersion.project_id == project_id).scalar()
        return int(current or 0) + 1
    finally:
        db.close()


class IndexingService:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.running_jobs: set[str] = set()
        self.cancelled_jobs: set[str] = set()
        self.worker_task: asyncio.Task | None = None
        self.watcher_task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.last_poll_at: dict[str, float] = {}

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        if not self.worker_task:
            self.worker_task = asyncio.create_task(self._worker_loop())
        if not self.watcher_task:
            self.watcher_task = asyncio.create_task(self._watcher_loop())

    async def stop(self) -> None:
        for task in [self.worker_task, self.watcher_task]:
            if task:
                task.cancel()
        self.worker_task = None
        self.watcher_task = None

    async def enqueue(self, project_id: str) -> None:
        await self.queue.put(project_id)

    async def drop_queued(self, project_id: str) -> None:
        pending: list[str] = []
        while True:
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item != project_id:
                pending.append(item)
            self.queue.task_done()
        for item in pending:
            await self.queue.put(item)

    def mark_cancelled(self, project_id: str) -> None:
        self.cancelled_jobs.add(project_id)

    def is_running(self, project_id: str) -> bool:
        return project_id in self.running_jobs

    async def _worker_loop(self) -> None:
        while True:
            project_id = await self.queue.get()
            if project_id in self.running_jobs:
                self.queue.task_done()
                continue
            if project_id in self.cancelled_jobs:
                self.queue.task_done()
                continue
            self.running_jobs.add(project_id)
            try:
                await asyncio.to_thread(self._run_full_reindex, project_id)
            finally:
                self.running_jobs.discard(project_id)
                self.queue.task_done()

    async def _watcher_loop(self) -> None:
        while True:
            await asyncio.sleep(max(10, settings.auto_reindex_interval_seconds))
            await asyncio.to_thread(self._watch_projects)

    def _watch_projects(self) -> None:
        db = SessionLocal()
        try:
            projects = db.query(Project).filter(Project.auto_reindex_enabled.is_(True)).all()
            now = time.time()
            for project in projects:
                project_id = str(project.id)
                last_poll = self.last_poll_at.get(project_id, 0)
                if now - last_poll < max(15, project.poll_interval_seconds):
                    continue
                self.last_poll_at[project_id] = now

                try:
                    repo_path, _ = resolve_source_path(project, sync_remote=True)
                    fingerprint = compute_fingerprint(repo_path)
                except Exception:
                    continue

                if fingerprint != project.last_source_fingerprint and project_id not in self.running_jobs:
                    project.last_source_fingerprint = fingerprint
                    db.commit()
                    if self.loop and self.loop.is_running():
                        asyncio.run_coroutine_threadsafe(self.enqueue(project_id), self.loop)
        finally:
            db.close()

    def _run_full_reindex(self, project_id: str) -> None:
        if project_id in self.cancelled_jobs:
            return
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                return

            version_num = _next_version(project.id)
            version = ProjectVersion(
                project_id=project.id,
                version=version_num,
                status=VersionStatus.INDEXING,
                stage="queued",
                progress_percent=2,
                started_at=datetime.utcnow(),
            )
            db.add(version)
            db.commit()
            db.refresh(version)

            def set_progress(
                *,
                stage: str,
                percent: int,
                processed_files: int | None = None,
                total_files: int | None = None,
            ) -> None:
                version.stage = stage
                version.progress_percent = max(0, min(100, percent))
                if processed_files is not None:
                    version.processed_files = processed_files
                if total_files is not None:
                    version.total_files = total_files
                db.commit()

            try:
                set_progress(stage="syncing-source", percent=6)
                repo_path, commit_hash = resolve_source_path(project, sync_remote=True)
                all_files = _python_files(repo_path)
                total_files = len(all_files)
                set_progress(stage="parsing", percent=10, processed_files=0, total_files=total_files)

                last_parse_report = 0

                def parse_progress(done: int, total: int) -> None:
                    nonlocal last_parse_report
                    if total <= 0:
                        return
                    threshold = max(1, total // 40)
                    if done != total and done - last_parse_report < threshold:
                        return
                    last_parse_report = done
                    progress = 10 + int((done / total) * 50)
                    set_progress(stage="parsing", percent=progress, processed_files=done, total_files=total)

                files, edges = parse_repo(
                    repo_path,
                    progress_callback=parse_progress,
                    ignored_dirnames=settings.ast_excluded_dirnames,
                )
                fingerprint = compute_fingerprint(repo_path)
                set_progress(stage="writing-graph", percent=65, processed_files=total_files, total_files=total_files)

                def write_progress(done_batches: int, total_batches: int, phase: str) -> None:
                    if total_batches <= 0:
                        return
                    progress = 65 + int((done_batches / total_batches) * 30)
                    set_progress(stage=phase, percent=progress, processed_files=total_files, total_files=total_files)

                graph_store.write_graph(
                    group_slug=project.group_slug,
                    display_name=project.display_name,
                    version=version_num,
                    files=files,
                    edges=edges,
                    commit_hash=commit_hash,
                    progress_callback=write_progress,
                )

                project.last_source_fingerprint = fingerprint
                if project.source_type == SourceType.GIT:
                    project.cloned_path = str(repo_path)

                version.status = VersionStatus.READY
                version.stage = "ready"
                version.progress_percent = 100
                version.processed_files = total_files
                version.total_files = total_files
                version.files_count = len(files)
                version.edges_count = len(edges)
                version.source_fingerprint = fingerprint
                version.commit_hash = commit_hash
                version.completed_at = datetime.utcnow()
                db.commit()
            except Exception as exc:
                version.status = VersionStatus.FAILED
                version.stage = "failed"
                version.error_message = str(exc)
                version.completed_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()


indexing_service = IndexingService()
