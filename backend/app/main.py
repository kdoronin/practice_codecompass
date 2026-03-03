from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, get_db
from .graph_store import graph_store
from .mcp_runtime import mcp_manager
from .models import Project, ProjectVersion, SourceType, VersionStatus
from .schemas import (
    CreateGitProjectRequest,
    CreateLocalProjectRequest,
    GraphViewOut,
    McpConfigSnippet,
    McpConfigsOut,
    McpStartRequest,
    McpStatusOut,
    ProjectOut,
    ProjectVersionOut,
    ReindexRequest,
    UpdateProjectRequest,
)
from .services import detect_default_branch, ensure_local_path_allowed, indexing_service
from .slug import slugify


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE project_versions ADD COLUMN IF NOT EXISTS stage VARCHAR(64) DEFAULT 'pending' NOT NULL"))
        connection.execute(text("ALTER TABLE project_versions ADD COLUMN IF NOT EXISTS progress_percent INTEGER DEFAULT 0 NOT NULL"))
        connection.execute(text("ALTER TABLE project_versions ADD COLUMN IF NOT EXISTS processed_files INTEGER DEFAULT 0 NOT NULL"))
        connection.execute(text("ALTER TABLE project_versions ADD COLUMN IF NOT EXISTS total_files INTEGER DEFAULT 0 NOT NULL"))
        connection.execute(
            text(
                """
                UPDATE project_versions
                SET stage = 'ready',
                    progress_percent = CASE WHEN progress_percent < 100 THEN 100 ELSE progress_percent END,
                    processed_files = CASE WHEN processed_files = 0 AND files_count > 0 THEN files_count ELSE processed_files END,
                    total_files = CASE WHEN total_files = 0 AND files_count > 0 THEN files_count ELSE total_files END
                WHERE status = 'READY'
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE project_versions
                SET stage = 'failed'
                WHERE status = 'FAILED'
                  AND (stage = 'pending' OR stage = '')
                """
            )
        )
    settings.repos_storage_root.mkdir(parents=True, exist_ok=True)
    await indexing_service.start()
    yield
    await indexing_service.stop()
    graph_store.close()


app = FastAPI(title="CodeCompass Platform API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalize_stage_and_progress(version: ProjectVersion) -> tuple[str, int, int, int]:
    stage = version.stage or "pending"
    progress = version.progress_percent or 0
    processed_files = version.processed_files or 0
    total_files = version.total_files or 0

    if version.status == VersionStatus.READY:
        stage = "ready"
        if progress < 100:
            progress = 100
        if total_files == 0 and version.files_count > 0:
            total_files = version.files_count
        if processed_files == 0 and total_files > 0:
            processed_files = total_files
    elif version.status == VersionStatus.FAILED and stage in {"pending", ""}:
        stage = "failed"

    return stage, progress, processed_files, total_files


def _project_to_out(project: Project, db: Session) -> ProjectOut:
    latest_obj = (
        db.query(ProjectVersion)
        .filter(ProjectVersion.project_id == project.id)
        .order_by(ProjectVersion.version.desc())
        .first()
    )
    latest_stage = None
    latest_progress = None
    latest_status = None
    if latest_obj:
        latest_stage, latest_progress, _, _ = _normalize_stage_and_progress(latest_obj)
        latest_status = latest_obj.status.value

    return ProjectOut(
        id=project.id,
        display_name=project.display_name,
        group_slug=project.group_slug,
        source_type=project.source_type.value,
        local_path=project.local_path,
        repo_url=project.repo_url,
        cloned_path=project.cloned_path,
        default_branch=project.default_branch,
        auto_reindex_enabled=project.auto_reindex_enabled,
        poll_interval_seconds=project.poll_interval_seconds,
        created_at=project.created_at,
        updated_at=project.updated_at,
        latest_version=latest_obj.version if latest_obj else None,
        latest_version_status=latest_status,
        latest_version_stage=latest_stage,
        latest_version_progress_percent=latest_progress,
    )


def _version_to_out(version: ProjectVersion) -> ProjectVersionOut:
    stage, progress, processed_files, total_files = _normalize_stage_and_progress(version)
    return ProjectVersionOut(
        version=version.version,
        status=version.status.value,
        stage=stage,
        progress_percent=progress,
        processed_files=processed_files,
        total_files=total_files,
        files_count=version.files_count,
        edges_count=version.edges_count,
        commit_hash=version.commit_hash,
        source_fingerprint=version.source_fingerprint,
        started_at=version.started_at,
        completed_at=version.completed_at,
        error_message=version.error_message,
    )


def _resolve_version(db: Session, project: Project, version: str | int) -> int:
    if str(version) == "latest":
        latest_ready = (
            db.query(func.max(ProjectVersion.version))
            .filter(
                ProjectVersion.project_id == project.id,
                ProjectVersion.status == VersionStatus.READY,
            )
            .scalar()
        )
        if latest_ready is None:
            latest_any = (
                db.query(func.max(ProjectVersion.version))
                .filter(ProjectVersion.project_id == project.id)
                .scalar()
            )
            if latest_any is None:
                raise HTTPException(status_code=404, detail="No versions available")
            raise HTTPException(
                status_code=409,
                detail="No READY graph version yet. Indexing is still in progress.",
            )
        return int(latest_ready)

    try:
        v = int(version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="version must be integer or 'latest'") from exc

    exists = (
        db.query(ProjectVersion)
        .filter(ProjectVersion.project_id == project.id, ProjectVersion.version == v)
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Version {v} not found")
    if exists.status != VersionStatus.READY:
        raise HTTPException(
            status_code=409,
            detail=f"Version {v} is not ready yet (status: {exists.status.value})",
        )
    return v


def _unique_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    idx = 2
    while db.query(Project).filter(Project.group_slug == slug).first():
        slug = f"{base_slug}-{idx}"
        idx += 1
    return slug


def _resolve_local_browse_path(path_value: str | None) -> Path:
    root = settings.allowed_local_root
    if path_value:
        candidate = Path(path_value)
        if not candidate.is_absolute():
            candidate = root / candidate
    else:
        candidate = root

    resolved = candidate.resolve()
    if root not in [resolved, *resolved.parents]:
        raise HTTPException(status_code=400, detail="Path is outside ALLOWED_LOCAL_ROOT")
    if not resolved.exists() or not resolved.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    return resolved


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "allowed_local_root": str(settings.allowed_local_root),
        "repos_storage_root": str(settings.repos_storage_root),
    }


@app.get("/local-folders")
def local_folders(path: str | None = Query(default=None)) -> dict:
    current = _resolve_local_browse_path(path)
    root = settings.allowed_local_root
    parent = str(current.parent) if current != root else None

    directories = []
    try:
        for child in sorted(current.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            try:
                has_children = any(grandchild.is_dir() for grandchild in child.iterdir())
            except (PermissionError, OSError):
                has_children = False
            directories.append(
                {
                    "name": child.name,
                    "path": str(child.resolve()),
                    "has_children": has_children,
                }
            )
    except (PermissionError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read directory: {exc}") from exc

    return {
        "root": str(root),
        "current": str(current),
        "parent": parent,
        "directories": directories,
    }


@app.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [_project_to_out(p, db) for p in projects]


@app.post("/projects/local", response_model=ProjectOut)
async def create_local_project(payload: CreateLocalProjectRequest, db: Session = Depends(get_db)):
    local_path = ensure_local_path_allowed(payload.local_path)
    display_name = payload.display_name or local_path.name
    group_slug = _unique_slug(db, slugify(display_name))

    project = Project(
        display_name=display_name,
        group_slug=group_slug,
        source_type=SourceType.LOCAL,
        local_path=str(local_path),
        default_branch="local",
        auto_reindex_enabled=payload.auto_reindex_enabled,
        poll_interval_seconds=max(15, payload.poll_interval_seconds),
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    await indexing_service.enqueue(str(project.id))
    return _project_to_out(project, db)


@app.post("/projects/git", response_model=ProjectOut)
async def create_git_project(payload: CreateGitProjectRequest, db: Session = Depends(get_db)):
    if not payload.repo_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Only public HTTPS git repos are supported")

    repo_name = payload.repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    display_name = payload.display_name or repo_name
    group_slug = _unique_slug(db, slugify(display_name))
    default_branch = detect_default_branch(payload.repo_url)

    project = Project(
        display_name=display_name,
        group_slug=group_slug,
        source_type=SourceType.GIT,
        repo_url=payload.repo_url,
        default_branch=default_branch,
        auto_reindex_enabled=payload.auto_reindex_enabled,
        poll_interval_seconds=max(15, payload.poll_interval_seconds),
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    await indexing_service.enqueue(str(project.id))
    return _project_to_out(project, db)


@app.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: UUID, payload: UpdateProjectRequest, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if payload.display_name:
        project.display_name = payload.display_name
        graph_store.update_group_display_name(project.group_slug, payload.display_name)

    if payload.group_slug:
        new_slug = slugify(payload.group_slug)
        if new_slug != project.group_slug:
            if db.query(Project).filter(Project.group_slug == new_slug).first():
                raise HTTPException(status_code=409, detail="group_slug already exists")
            old_slug = project.group_slug
            project.group_slug = new_slug
            graph_store.rename_group_slug(old_slug, new_slug)

    if payload.auto_reindex_enabled is not None:
        project.auto_reindex_enabled = payload.auto_reindex_enabled

    if payload.poll_interval_seconds is not None:
        project.poll_interval_seconds = max(15, payload.poll_interval_seconds)

    db.commit()
    db.refresh(project)
    return _project_to_out(project, db)


@app.delete("/projects/{project_id}")
async def delete_project(project_id: UUID, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await indexing_service.drop_queued(str(project.id))
    if indexing_service.is_running(str(project.id)):
        raise HTTPException(status_code=409, detail="Project is indexing now. Retry after indexing completes.")

    indexing_service.mark_cancelled(str(project.id))
    group_slug = project.group_slug
    db.delete(project)
    db.commit()
    graph_store.delete_group(group_slug)
    return {"deleted": True, "project_id": str(project_id), "group_slug": group_slug}


@app.get("/projects/{project_id}/versions", response_model=list[ProjectVersionOut])
def list_versions(project_id: UUID, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    versions = (
        db.query(ProjectVersion)
        .filter(ProjectVersion.project_id == project.id)
        .order_by(ProjectVersion.version.desc())
        .all()
    )
    return [_version_to_out(v) for v in versions]


@app.post("/projects/{project_id}/reindex")
async def reindex_project(project_id: UUID, _: ReindexRequest, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await indexing_service.enqueue(str(project.id))
    return {"queued": True, "group_slug": project.group_slug}


@app.post("/internal/reindex/{group_slug}")
async def reindex_by_group_slug(group_slug: str, _: ReindexRequest, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.group_slug == group_slug).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await indexing_service.enqueue(str(project.id))
    return {"queued": True, "group_slug": project.group_slug}


@app.get("/projects/{project_id}/graph", response_model=GraphViewOut)
def get_graph(
    project_id: UUID,
    version: str = Query(default="latest"),
    mode: Literal["full", "subgraph"] = Query(default="full"),
    file_path: str | None = Query(default=None),
    depth: int = Query(default=1, ge=1, le=5),
    node_limit: int = Query(default=1500, ge=100, le=10000),
    edge_limit: int = Query(default=3000, ge=100, le=25000),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if mode == "subgraph" and not file_path:
        raise HTTPException(status_code=400, detail="file_path is required for subgraph mode")

    resolved_version = _resolve_version(db, project, version)

    if mode == "full":
        node_limit = min(node_limit, settings.max_graph_nodes_full_view)
        edge_limit = min(edge_limit, settings.max_graph_edges_full_view)

    nodes, edges, truncated = graph_store.get_graph(
        group_slug=project.group_slug,
        version=resolved_version,
        mode=mode,
        file_path=file_path,
        depth=depth,
        node_limit=node_limit,
        edge_limit=edge_limit,
    )

    message = None
    if truncated:
        message = (
            f"Graph is truncated to {node_limit} nodes / {edge_limit} edges. "
            "Use subgraph mode or raise limits in .env"
        )

    return GraphViewOut(
        group_slug=project.group_slug,
        version=resolved_version,
        mode=mode,
        nodes=nodes,
        edges=edges,
        truncated=truncated,
        limit_message=message,
    )


@app.post("/mcp/start", response_model=McpStatusOut)
def start_mcp(payload: McpStartRequest):
    mcp_manager.start(
        payload.default_group_slug,
        payload.default_version,
        transport=payload.transport,
        host=payload.host,
        port=payload.port,
        path=payload.path,
        public_url=payload.public_url,
        stateless_http=payload.stateless_http,
    )
    return McpStatusOut(**mcp_manager.status())


@app.post("/mcp/stop", response_model=McpStatusOut)
def stop_mcp():
    mcp_manager.stop()
    return McpStatusOut(**mcp_manager.status())


@app.get("/mcp/status", response_model=McpStatusOut)
def mcp_status():
    return McpStatusOut(**mcp_manager.status())


@app.get("/mcp/logs")
def mcp_logs():
    return {"lines": mcp_manager.get_logs()}


@app.get("/mcp/configs", response_model=McpConfigsOut)
def mcp_configs(
    group_slug: str | None = Query(default=None),
    version: str = Query(default="latest"),
    transport: str = Query(default=settings.mcp_default_transport),
    host: str = Query(default=settings.mcp_http_host),
    port: int = Query(default=settings.mcp_http_port),
    path: str = Query(default=settings.mcp_http_path),
    public_url: str | None = Query(default=None),
):
    path_normalized = path if path.startswith("/") else f"/{path}"
    url = public_url or settings.mcp_http_public_url or f"http://localhost:{port}{path_normalized}"

    cmd = ["python", "/app/mcp_server.py"]
    if group_slug:
        cmd.extend(["--default-group-slug", group_slug])
    if version:
        cmd.extend(["--default-version", version])
    cmd.extend(
        [
            "--transport",
            transport,
            "--host",
            host,
            "--port",
            str(port),
            "--path",
            path_normalized,
        ]
    )

    raw = " ".join(cmd)

    return McpConfigsOut(
        items=[
            McpConfigSnippet(
                provider="Claude Code",
                description="Remote URL connection (preferred for external clients)",
                snippet=f"claude mcp add --transport streamable-http codecompass {url}",
            ),
            McpConfigSnippet(
                provider="Cursor",
                description="Add to ~/.cursor/mcp.json",
                snippet=(
                    '{\n'
                    '  "mcpServers": {\n'
                    '    "codecompass": {\n'
                    f'      "url": "{url}"\n'
                    '    }\n'
                    '  }\n'
                    '}'
                ),
            ),
            McpConfigSnippet(
                provider="Cline",
                description="Add this server command in Cline MCP settings",
                snippet=(
                    '{\n'
                    '  "name": "codecompass",\n'
                    f'  "url": "{url}"\n'
                    '}'
                ),
            ),
            McpConfigSnippet(
                provider="Codex CLI",
                description="Example mcp server block",
                snippet=(
                    '{\n'
                    '  "mcp_servers": {\n'
                    '    "codecompass": {\n'
                    f'      "url": "{url}"\n'
                    '    }\n'
                    '  }\n'
                    '}'
                ),
            ),
            McpConfigSnippet(
                provider="Local Command Fallback",
                description="Use this if URL transport is unavailable in your client",
                snippet=raw,
            ),
        ]
    )
