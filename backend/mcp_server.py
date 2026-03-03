from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastmcp import FastMCP
from sqlalchemy import func

from app.db import SessionLocal
from app.graph_store import graph_store
from app.models import Project, ProjectVersion, VersionStatus


API_INTERNAL_URL = os.getenv("API_INTERNAL_URL", "http://localhost:8000")

parser = argparse.ArgumentParser(description="CodeCompass MCP server")
parser.add_argument("--default-group-slug", default=os.getenv("MCP_DEFAULT_GROUP_SLUG"))
parser.add_argument("--default-version", default=os.getenv("MCP_DEFAULT_VERSION", "latest"))
parser.add_argument("--transport", default=os.getenv("MCP_DEFAULT_TRANSPORT", "stdio"))
parser.add_argument("--host", default=os.getenv("MCP_HTTP_HOST", "0.0.0.0"))
parser.add_argument("--port", type=int, default=int(os.getenv("MCP_HTTP_PORT", "8811")))
parser.add_argument("--path", default=os.getenv("MCP_HTTP_PATH", "/mcp"))
parser.add_argument("--stateless-http", default=os.getenv("MCP_HTTP_STATELESS", "true"))
args = parser.parse_args()

DEFAULT_GROUP = args.default_group_slug
DEFAULT_VERSION = args.default_version
TRANSPORT = str(args.transport or "stdio").strip().lower()
HTTP_HOST = args.host
HTTP_PORT = int(args.port)
HTTP_PATH = args.path if str(args.path).startswith("/") else f"/{args.path}"
STATELESS_HTTP = str(args.stateless_http).strip().lower() in {"1", "true", "yes", "on"}

mcp = FastMCP("CodeCompass")


def _select_group(group_slug: str | None) -> str:
    selected = group_slug or DEFAULT_GROUP
    if not selected:
        raise ValueError("group_slug is required (or configure --default-group-slug)")
    return selected


def _select_version(group_slug: str, version: str | int | None) -> int:
    selected = version if version is not None else DEFAULT_VERSION
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.group_slug == group_slug).first()
        if not project:
            raise ValueError(f"Unknown group_slug: {group_slug}")

        if str(selected) == "latest":
            latest_ready = (
                db.query(func.max(ProjectVersion.version))
                .filter(
                    ProjectVersion.project_id == project.id,
                    ProjectVersion.status == VersionStatus.READY,
                )
                .scalar()
            )
            if latest_ready is None:
                raise ValueError(f"No READY versions available for group {group_slug}")
            return int(latest_ready)

        explicit_version = int(selected)
        explicit_obj = (
            db.query(ProjectVersion)
            .filter(
                ProjectVersion.project_id == project.id,
                ProjectVersion.version == explicit_version,
            )
            .first()
        )
        if not explicit_obj:
            raise ValueError(f"Version {explicit_version} not found for group {group_slug}")
        if explicit_obj.status != VersionStatus.READY:
            raise ValueError(f"Version {explicit_version} is not ready yet (status: {explicit_obj.status.value})")
        return explicit_version
    finally:
        db.close()


@mcp.tool()
def list_groups() -> str:
    """List available graph groups and their latest indexed version."""
    db = SessionLocal()
    try:
        projects = db.query(Project).order_by(Project.group_slug.asc()).all()
        if not projects:
            return "No groups found"

        lines = ["Available groups:\n"]
        for project in projects:
            latest = (
                db.query(func.max(ProjectVersion.version))
                .filter(ProjectVersion.project_id == project.id)
                .scalar()
            )
            latest_ready = (
                db.query(func.max(ProjectVersion.version))
                .filter(
                    ProjectVersion.project_id == project.id,
                    ProjectVersion.status == VersionStatus.READY,
                )
                .scalar()
            )
            lines.append(
                f"- {project.group_slug} (name: {project.display_name}, latest: {latest or 'none'}, latest_ready: {latest_ready or 'none'}, source: {project.source_type.value})"
            )
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool()
def get_file_node(file_path: str, group_slug: str | None = None, version: str | int | None = None) -> str:
    """Get a single file node by path inside a group/version graph."""
    try:
        slug = _select_group(group_slug)
        resolved_version = _select_version(slug, version)
        node = graph_store.get_file_node(slug, resolved_version, file_path)
        if not node:
            return f"File node not found: {file_path} in {slug}@v{resolved_version}"
        return json.dumps({"group_slug": slug, "version": resolved_version, "node": node}, indent=2)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def neighbors(
    file_path: str,
    group_slug: str | None = None,
    version: str | int | None = None,
    direction: str = "both",
    limit: int = 200,
) -> str:
    """Return structural neighbors (IMPORTS/INHERITS/INSTANTIATES) for a file in selected group/version."""
    try:
        slug = _select_group(group_slug)
        resolved_version = _select_version(slug, version)
        rows = graph_store.neighbors(slug, resolved_version, file_path, direction=direction, limit=max(1, limit))

        if not rows:
            return f"No neighbors found for {file_path} in {slug}@v{resolved_version}"

        lines = [f"Neighbors for {file_path} in {slug}@v{resolved_version}:\n"]
        for row in rows:
            symbol = "→" if row["direction"] == "outgoing" else "←"
            lines.append(f"  {symbol} [{row['relation']}] {row['neighbor']}")
        lines.append(f"\nTotal: {len(rows)}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def subgraph(
    file_path: str,
    group_slug: str | None = None,
    version: str | int | None = None,
    depth: int = 1,
    limit: int = 300,
) -> str:
    """Return a bounded subgraph around a file."""
    try:
        slug = _select_group(group_slug)
        resolved_version = _select_version(slug, version)
        payload = graph_store.subgraph(slug, resolved_version, file_path, depth=max(1, depth), limit=max(50, limit))
        return json.dumps(
            {
                "group_slug": slug,
                "version": resolved_version,
                "file_path": file_path,
                **payload,
            },
            indent=2,
        )
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def get_architectural_context(
    file_path: str,
    group_slug: str | None = None,
    version: str | int | None = None,
    direction: str = "both",
    limit: int = 200,
) -> str:
    """Alias for neighbors() with architecture-focused wording."""
    return neighbors(
        file_path=file_path,
        group_slug=group_slug,
        version=version,
        direction=direction,
        limit=limit,
    )


@mcp.tool()
def reindex(group_slug: str | None = None) -> str:
    """Trigger full reindex for selected group in the admin API."""
    try:
        slug = _select_group(group_slug)
        url = f"{API_INTERNAL_URL.rstrip('/')}/internal/reindex/{slug}"
        req = urllib.request.Request(
            url,
            method="POST",
            data=json.dumps({"reason": "mcp"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode("utf-8")
        return f"Reindex queued for {slug}: {payload}"
    except urllib.error.HTTPError as exc:
        return f"Failed to queue reindex: {exc.code} {exc.reason}"
    except Exception as exc:
        return f"Failed to queue reindex: {exc}"


if __name__ == "__main__":
    print("Starting CodeCompass MCP server")
    print(f"Default group: {DEFAULT_GROUP or '(none)'}")
    print(f"Default version: {DEFAULT_VERSION}")
    print(f"Transport: {TRANSPORT}")
    if TRANSPORT == "stdio":
        mcp.run(transport="stdio")
    else:
        kwargs = {
            "transport": TRANSPORT,
            "host": HTTP_HOST,
            "port": HTTP_PORT,
            "path": HTTP_PATH,
        }
        if TRANSPORT != "sse":
            kwargs["stateless_http"] = STATELESS_HTTP
        mcp.run(**kwargs)
