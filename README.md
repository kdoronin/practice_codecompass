# CodeCompass Platform (New Root Docker Project)

This is a standalone multi-service platform built in the repository root.

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

2. Start stack:

```bash
docker compose up --build -d
```

3. Open admin panel:

- Web: http://localhost:5173
- API docs: http://localhost:8000/docs
- Neo4j Browser: http://localhost:7474

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
