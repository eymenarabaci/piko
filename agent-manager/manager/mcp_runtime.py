"""Bounded stdio MCP client for explicitly enabled bundled Node servers."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .mcp_catalog import SPECS


class MCPRuntimeError(RuntimeError):
    """A sanitized MCP connection or protocol error."""


class MCPRuntime:
    def __init__(self, workspace_root: Path, *, enabled_servers: set[str] | None = None,
                 timeout: float = 30.0):
        self.workspace_root = Path(workspace_root).resolve()
        self.enabled_servers = frozenset(enabled_servers or ())
        self.timeout = min(max(float(timeout), 1.0), 120.0)

    def _command(self, server_id: str) -> tuple[str, str]:
        if server_id not in SPECS or server_id not in self.enabled_servers:
            raise MCPRuntimeError("MCP sunucusu etkin değil")
        node = shutil.which("node")
        if not node:
            raise MCPRuntimeError("Node.js bulunamadı")
        package = SPECS[server_id][1]
        folder = self.workspace_root / "tools" / "mcp-packages" / "node_modules" / package
        try:
            meta = json.loads((folder / "package.json").read_text(encoding="utf-8"))
            bins = meta["bin"]
            relative = bins if isinstance(bins, str) else next(iter(bins.values()))
            entry = (folder / relative).resolve(strict=True)
            if not entry.is_file() or not entry.is_relative_to(folder.resolve()):
                raise ValueError("invalid package entry")
        except (OSError, KeyError, ValueError, TypeError, StopIteration):
            raise MCPRuntimeError("MCP paketi kurulu değil veya giriş dosyası geçersiz") from None
        return node, str(entry)

    @staticmethod
    def _environment() -> dict[str, str]:
        # Do not pass API keys or other ambient credentials into a Node server.
        allowed = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE",
                   "APPDATA", "LOCALAPPDATA", "HOME", "COMSPEC")
        return {key: os.environ[key] for key in allowed if key in os.environ}

    async def _request(self, process: asyncio.subprocess.Process, request_id: int,
                       method: str, params: dict[str, Any]) -> dict[str, Any]:
        if process.stdin is None or process.stdout is None:
            raise MCPRuntimeError("MCP stdio bağlantısı açılamadı")
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        await asyncio.wait_for(process.stdin.drain(), self.timeout)
        for _ in range(32):
            line = await asyncio.wait_for(process.stdout.readline(), self.timeout)
            if not line:
                raise MCPRuntimeError("MCP sunucusu yanıt vermeden kapandı")
            try:
                reply = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                raise MCPRuntimeError("MCP sunucusu geçersiz protokol yanıtı verdi") from None
            if reply.get("id") != request_id:
                continue  # Progress/notification messages may arrive before the reply.
            if "error" in reply:
                raise MCPRuntimeError(f"MCP {method} çağrısı başarısız")
            result = reply.get("result")
            if not isinstance(result, dict):
                raise MCPRuntimeError("MCP sunucusu geçersiz sonuç döndürdü")
            return result
        raise MCPRuntimeError("MCP sunucusu çok fazla ara bildirim gönderdi")

    @asynccontextmanager
    async def _session(self, server_id: str):
        node, entry = self._command(server_id)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = await asyncio.create_subprocess_exec(
                node, entry, cwd=str(self.workspace_root), env=self._environment(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=2 * 1024 * 1024,
                creationflags=flags,
            )
        except OSError:
            raise MCPRuntimeError("MCP sunucusu başlatılamadı") from None
        try:
            await self._request(proc, 1, "initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "agent-manager", "version": "1.0.0"},
            })
            assert proc.stdin is not None
            proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            await asyncio.wait_for(proc.stdin.drain(), self.timeout)
            yield proc
        except asyncio.TimeoutError:
            raise MCPRuntimeError("MCP sunucusu süre sınırına takıldı") from None
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), 3.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            elif proc.returncode is not None:
                await proc.wait()

    async def list_tools(self, server_id: str) -> list[dict[str, Any]]:
        async with self._session(server_id) as proc:
            tools: list[dict[str, Any]] = []
            cursor = None
            for page in range(3):
                params = {"cursor": cursor} if cursor else {}
                result = await self._request(proc, 2 + page, "tools/list", params)
                tools.extend(x for x in result.get("tools", []) if isinstance(x, dict))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            return tools

    async def call_tool(self, server_id: str, tool_name: str,
                        arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise MCPRuntimeError("MCP araç argümanları nesne olmalı")
        async with self._session(server_id) as proc:
            catalog = await self._request(proc, 2, "tools/list", {})
            names = {tool.get("name") for tool in catalog.get("tools", []) if isinstance(tool, dict)}
            if tool_name not in names:
                raise MCPRuntimeError("MCP aracı bu sunucuda bulunamadı")
            try:
                return await self._request(proc, 3, "tools/call", {
                    "name": tool_name, "arguments": arguments,
                })
            except asyncio.TimeoutError:
                raise MCPRuntimeError("MCP araç çağrısı süre sınırına takıldı") from None
