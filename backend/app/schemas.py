from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class CreateLocalProjectRequest(BaseModel):
    display_name: str | None = None
    local_path: str = Field(..., description="Absolute host path mounted under ALLOWED_LOCAL_ROOT")
    auto_reindex_enabled: bool = True
    poll_interval_seconds: int = 60


class CreateGitProjectRequest(BaseModel):
    display_name: str | None = None
    repo_url: str
    auto_reindex_enabled: bool = True
    poll_interval_seconds: int = 60


class UpdateProjectRequest(BaseModel):
    display_name: str | None = None
    group_slug: str | None = None
    auto_reindex_enabled: bool | None = None
    poll_interval_seconds: int | None = None


class ReindexRequest(BaseModel):
    reason: str = "manual"


class ProjectVersionOut(BaseModel):
    version: int
    status: str
    stage: str
    progress_percent: int
    processed_files: int
    total_files: int
    files_count: int
    edges_count: int
    commit_hash: str | None
    source_fingerprint: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None


class ProjectOut(BaseModel):
    id: UUID
    display_name: str
    group_slug: str
    source_type: str
    local_path: str | None
    repo_url: str | None
    cloned_path: str | None
    default_branch: str
    auto_reindex_enabled: bool
    poll_interval_seconds: int
    created_at: datetime
    updated_at: datetime
    latest_version: int | None
    latest_version_status: str | None = None
    latest_version_stage: str | None = None
    latest_version_progress_percent: int | None = None


class GraphNodeOut(BaseModel):
    id: str
    label: str
    path: str


class GraphEdgeOut(BaseModel):
    id: str
    source: str
    target: str
    relation: str


class GraphViewOut(BaseModel):
    group_slug: str
    version: int
    mode: Literal["full", "subgraph"]
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    truncated: bool
    limit_message: str | None = None


class McpStartRequest(BaseModel):
    default_group_slug: str | None = None
    default_version: str | int = "latest"
    transport: str | None = None
    host: str | None = None
    port: int | None = None
    path: str | None = None
    public_url: str | None = None
    stateless_http: bool | None = None


class McpStatusOut(BaseModel):
    running: bool
    pid: int | None
    started_at: datetime | None
    command: str | None
    default_group_slug: str | None
    default_version: str | int | None
    transport: str | None
    host: str | None
    port: int | None
    path: str | None
    url: str | None
    stateless_http: bool | None


class McpConfigSnippet(BaseModel):
    provider: str
    description: str
    snippet: str


class McpConfigsOut(BaseModel):
    items: list[McpConfigSnippet]
