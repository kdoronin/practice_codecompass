# CodeCompass Platform (New Root Docker Project)

This is a standalone multi-service platform built in the repository root.

## Inspiration

This project is inspired by:  
https://github.com/tpaip607/research-codecompass

## Services

- `api` (FastAPI): project registry, versioned indexing, graph API, MCP runtime controls
- `web` (React + Cytoscape): admin panel for onboarding projects, graph visualization, MCP controls
- `postgres`: metadata store for projects and version history
- `neo4j`: graph store for AST-based dependency graph

## Quick start

1. Create env file:

```bash
cp .env.example .env
# set HOST_PROJECTS_ROOT to a real absolute path on your machine
```

2. Configure `.env` values for your machine.

3. Start stack:

```bash
docker compose up --build -d
```

4. Open admin panel:

- Web: http://localhost:5173
- API docs: http://localhost:8000/docs
- Neo4j Browser: http://localhost:7474

## Environment Configuration (`.env`)

Copy template and edit:

```bash
cp .env.example .env
```

Core paths and DB:

- `HOST_PROJECTS_ROOT` - absolute host path mounted into container as `/host_projects`.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` - PostgreSQL metadata DB config.
- `NEO4J_USER`, `NEO4J_PASSWORD` - Neo4j auth.

Indexing and graph limits:

- `AUTO_REINDEX_INTERVAL_SECONDS` - global watcher interval.
- `DEFAULT_POLL_INTERVAL_SECONDS` - default per-project polling interval in UI.
- `MAX_GRAPH_NODES_FULL_VIEW` - cap for full graph nodes in API/UI.
- `MAX_GRAPH_EDGES_FULL_VIEW` - cap for full graph edges in API/UI.
- `AST_EXCLUDED_DIRNAMES` - comma-separated directories excluded from indexing (for example: `venv,.venv,site-packages`).

MCP runtime defaults:

- `MCP_DEFAULT_TRANSPORT` - `streamable-http`, `http`, `sse`, or `stdio`.
- `MCP_HTTP_HOST` - bind host for MCP HTTP transport (usually `0.0.0.0` in Docker).
- `MCP_HTTP_PORT` - MCP HTTP port exposed by Docker.
- `MCP_HTTP_PATH` - MCP endpoint path (for example `/mcp`).
- `MCP_HTTP_PUBLIC_URL` - URL shown in generated client snippets.
- `MCP_HTTP_STATELESS` - `true/false`, used for HTTP transports except SSE.

After changing `.env`, restart services:

```bash
docker compose up -d --build
```

## Supported workflows

- Add local Python project (from mounted `HOST_PROJECTS_ROOT`)
- Add public git repository (default branch auto-detected)
- Build graph in Neo4j under `group_slug + version`
- Keep version history and inspect older versions
- Reindex manually or automatically (polling)
- Visualize full graph or bounded subgraph in admin panel
- Start/stop MCP server in `stdio` or URL mode (`streamable-http` / `http` / `sse`), read logs, copy client config snippets

## MCP tools

Implemented in `backend/mcp_server.py`:

- `list_groups`
- `get_file_node`
- `neighbors`
- `subgraph`
- `get_architectural_context`
- `reindex`

You can set default `group_slug` / `version` via server args.

## Remote MCP by URL

Set these in `.env`:

- `MCP_DEFAULT_TRANSPORT=streamable-http`
- `MCP_HTTP_PORT=8811`
- `MCP_HTTP_PATH=/mcp`
- `MCP_HTTP_PUBLIC_URL=http://localhost:8811/mcp`

Then restart stack and start MCP from admin panel.  
External MCP clients can connect by URL from generated snippets in `MCP Control -> Client Configs`.
