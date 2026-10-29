"""Inventory the MCP packages bundled as reproducible dependencies.

An installed package is not proof that a server is configured or connected.
Only report packages whose executable entry point exists locally. Never read
or expose user credentials from MCP configuration in the public API.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path


SPECS = {
    "context7": ("documentation research", "@upstash/context7-mcp"),
    "playwright": ("browser testing frontend", "@playwright/mcp"),
    "shadcn": ("frontend components design", "shadcn"),
    "sequential-thinking": ("planning architecture reasoning", "@modelcontextprotocol/server-sequential-thinking"),
    "memory": ("memory knowledge graph", "@modelcontextprotocol/server-memory"),
    "sqlite": ("database sqlite analysis", "mcp-server-sqlite-npx"),
    "puppeteer": ("browser testing", "@modelcontextprotocol/server-puppeteer"),
    "magic-21st": ("frontend components design", "@21st-dev/magic"),
    "firebase": ("firebase cloud backend", "firebase-tools"),
    "github": ("git github repository", "@modelcontextprotocol/server-github"),
    "figma": ("design figma frontend", "figma-developer-mcp"),
    "supabase": ("database supabase backend", "@supabase/mcp-server-supabase"),
}

PUBLIC_AUTO_SERVERS = frozenset({"sequential-thinking", "context7"})


def get_server_config(workspace_root: Path, server_id: str) -> dict[str, object]:
    """Return a CAO stdio profile for a bundled, credential-free MCP server."""
    if server_id not in PUBLIC_AUTO_SERVERS:
        raise ValueError("MCP sunucusu otomatik kullanım için etkin değil")
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js bulunamadı")
    package = SPECS[server_id][1]
    folder = Path(workspace_root).resolve() / "tools" / "mcp-packages" / "node_modules" / package
    try:
        metadata = json.loads((folder / "package.json").read_text(encoding="utf-8"))
        bins = metadata["bin"]
        relative = bins if isinstance(bins, str) else next(iter(bins.values()))
        executable = (folder / relative).resolve(strict=True)
        if not executable.is_file() or not executable.is_relative_to(folder.resolve()):
            raise ValueError("invalid package entry")
    except (OSError, KeyError, ValueError, TypeError, StopIteration):
        raise ValueError("MCP paketi kurulu değil") from None
    return {"type": "stdio", "command": str(Path(node).resolve()), "args": [str(executable)]}


def list_installed_mcps(workspace_root: Path) -> list[dict[str, str]]:
    """Return verified local packages; tool connections are checked separately."""
    if not shutil.which("node"):
        return []
    package_root = Path(workspace_root) / "tools" / "mcp-packages" / "node_modules"
    result = []
    for server_id, (capabilities, package) in SPECS.items():
        folder = package_root / package
        try:
            metadata = json.loads((folder / "package.json").read_text(encoding="utf-8"))
            bins = metadata.get("bin", {})
            relative = bins if isinstance(bins, str) else next(iter(bins.values()))
            executable = (folder / relative).resolve()
            if not executable.is_file() or not executable.is_relative_to(folder.resolve()):
                continue
            result.append({"id": server_id, "capabilities": capabilities,
                           "status": "installed_unverified", "package": package,
                           "version": str(metadata.get("version", "unknown"))})
        except (OSError, ValueError, TypeError, StopIteration):
            continue
    return result
