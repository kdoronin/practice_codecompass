from __future__ import annotations

import shlex
import subprocess
import threading
from collections import deque
from datetime import datetime

from .config import settings


class McpManager:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.started_at: datetime | None = None
        self.command: str | None = None
        self.default_group_slug: str | None = None
        self.default_version: str | int | None = None
        self.transport: str | None = None
        self.host: str | None = None
        self.port: int | None = None
        self.path: str | None = None
        self.url: str | None = None
        self.stateless_http: bool | None = None
        self.logs = deque(maxlen=1000)

    def _consume(self, stream):
        while True:
            if stream is None:
                return
            line = stream.readline()
            if not line:
                return
            self.logs.append(line.rstrip())

    @staticmethod
    def _normalize_path(path: str | None) -> str:
        value = path or settings.mcp_http_path
        return value if value.startswith("/") else f"/{value}"

    @staticmethod
    def _build_url(host: str, port: int, path: str) -> str:
        return f"http://{host}:{port}{path}"

    def start(
        self,
        default_group_slug: str | None,
        default_version: str | int,
        transport: str | None = None,
        host: str | None = None,
        port: int | None = None,
        path: str | None = None,
        public_url: str | None = None,
        stateless_http: bool | None = None,
    ) -> None:
        if self.process and self.process.poll() is None:
            self.stop()

        resolved_transport = (transport or settings.mcp_default_transport).strip().lower()
        if resolved_transport not in {"stdio", "http", "streamable-http", "sse"}:
            raise ValueError("transport must be one of: stdio, http, streamable-http, sse")

        resolved_host = host or settings.mcp_http_host
        resolved_port = int(port or settings.mcp_http_port)
        resolved_path = self._normalize_path(path)
        resolved_stateless = settings.mcp_http_stateless if stateless_http is None else bool(stateless_http)
        resolved_url = public_url or settings.mcp_http_public_url
        if resolved_transport != "stdio" and not public_url:
            resolved_url = self._build_url("localhost", resolved_port, resolved_path)

        args = shlex.split(settings.mcp_default_command)
        if default_group_slug:
            args.extend(["--default-group-slug", default_group_slug])
        if default_version is not None:
            args.extend(["--default-version", str(default_version)])
        args.extend(["--transport", resolved_transport])
        if resolved_transport != "stdio":
            args.extend(["--host", resolved_host, "--port", str(resolved_port), "--path", resolved_path])
            if resolved_transport != "sse":
                args.extend(["--stateless-http", "true" if resolved_stateless else "false"])

        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if resolved_transport == "stdio" else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.started_at = datetime.utcnow()
        self.command = " ".join(args)
        self.default_group_slug = default_group_slug
        self.default_version = default_version
        self.transport = resolved_transport
        self.host = resolved_host if resolved_transport != "stdio" else None
        self.port = resolved_port if resolved_transport != "stdio" else None
        self.path = resolved_path if resolved_transport != "stdio" else None
        self.url = resolved_url if resolved_transport != "stdio" else None
        self.stateless_http = resolved_stateless if resolved_transport != "stdio" else None
        self.logs.append(f"[{self.started_at.isoformat()}] MCP server started")

        threading.Thread(target=self._consume, args=(self.process.stdout,), daemon=True).start()

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return

        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()

        self.logs.append(f"[{datetime.utcnow().isoformat()}] MCP server stopped")

    def status(self) -> dict:
        running = self.process is not None and self.process.poll() is None
        return {
            "running": running,
            "pid": self.process.pid if running and self.process else None,
            "started_at": self.started_at,
            "command": self.command,
            "default_group_slug": self.default_group_slug,
            "default_version": self.default_version,
            "transport": self.transport,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "url": self.url,
            "stateless_http": self.stateless_http,
        }

    def get_logs(self) -> list[str]:
        return list(self.logs)


mcp_manager = McpManager()
