from __future__ import annotations

import os
from pathlib import Path


def _csv_env(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _bool_env(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


class Settings:
    postgres_url: str = os.getenv(
        "POSTGRES_URL",
        "postgresql+psycopg2://codecompass:codecompass@postgres:5432/codecompass",
    )
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "research123")

    allowed_local_root: Path = Path(os.getenv("ALLOWED_LOCAL_ROOT", "/host_projects")).resolve()
    repos_storage_root: Path = Path(os.getenv("REPOS_STORAGE_ROOT", "/data/repos")).resolve()

    auto_reindex_interval_seconds: int = int(os.getenv("AUTO_REINDEX_INTERVAL_SECONDS", "60"))
    default_poll_interval_seconds: int = int(os.getenv("DEFAULT_POLL_INTERVAL_SECONDS", "60"))

    max_graph_nodes_full_view: int = int(os.getenv("MAX_GRAPH_NODES_FULL_VIEW", "3000"))
    max_graph_edges_full_view: int = int(os.getenv("MAX_GRAPH_EDGES_FULL_VIEW", "7000"))
    ast_excluded_dirnames: tuple[str, ...] = _csv_env(
        os.getenv(
            "AST_EXCLUDED_DIRNAMES",
            ".git,__pycache__,venv,.venv,site-packages,node_modules,dist,build,.mypy_cache,.pytest_cache,.tox",
        )
    )

    mcp_default_command: str = os.getenv("MCP_DEFAULT_COMMAND", "python /app/mcp_server.py")
    mcp_default_transport: str = os.getenv("MCP_DEFAULT_TRANSPORT", "streamable-http")
    mcp_http_host: str = os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    mcp_http_port: int = int(os.getenv("MCP_HTTP_PORT", "8811"))
    mcp_http_path: str = os.getenv("MCP_HTTP_PATH", "/mcp")
    mcp_http_public_url: str = os.getenv("MCP_HTTP_PUBLIC_URL", "http://localhost:8811/mcp")
    mcp_http_stateless: bool = _bool_env(os.getenv("MCP_HTTP_STATELESS"), True)


settings = Settings()
