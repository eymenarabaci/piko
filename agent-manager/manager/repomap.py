"""Aider-style structural repository map for Agent Manager.
Extracts symbols, exports, and key architecture into a compact in-memory map.
Eliminates 2-3 minutes of exploratory 'Get-ChildItem / find' command loops.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

EXCLUDED_DIRS = {
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "dist", "build", ".gradle", "target", "out", ".idea", ".vscode",
    "coverage", "logs", ".next", ".nuxt"
}

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".gradle", ".kt", ".java", ".md"
}

_cache: dict[str, tuple[float, str]] = {}


def extract_symbols_from_file(file_path: Path) -> list[str]:
    """Fast regex-based symbol and export extractor."""
    symbols = []
    suffix = file_path.suffix.lower()

    try:
        # Read at most first 200 lines to stay ultra-fast
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            lines = [f.readline() for _ in range(200)]
    except Exception:
        return []

    if suffix == ".py":
        for line in lines:
            m = re.match(r"^(?:async\s+def|def|class)\s+([a-zA-Z0-9_]+)", line.strip())
            if m:
                symbols.append(m.group(0))
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        for line in lines:
            m = re.match(r"^(?:export\s+(?:default\s+)?(?:function|class|const|let)|router\.(?:get|post|put|delete))\s+([a-zA-Z0-9_]+)", line.strip())
            if m:
                symbols.append(m.group(0))
    elif suffix == ".json" and file_path.name == "package.json":
        text = "".join(lines)
        if "scripts" in text:
            symbols.append("scripts: " + ", ".join(re.findall(r'"([^"]+)":\s*"[^"]*"', text[text.find("scripts"):text.find("scripts") + 250])[:5]))
    return symbols[:6]


def generate_repo_map(workspace_path: Path, max_depth: int = 3, max_chars: int = 2500) -> str:
    """Generate a clean, compact structural tree of the repository."""
    if not workspace_path.is_dir():
        return ""

    wp_str = str(workspace_path.resolve())
    now = time.time()

    # Cache check (valid for 30 seconds unless invalidated)
    if wp_str in _cache:
        cached_time, cached_map = _cache[wp_str]
        if now - cached_time < 30.0:
            return cached_map

    lines = [f"📂 Proje Kökü: {workspace_path.name}"]
    total_files = 0

    try:
        for root, dirs, files in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]
            rel_root = os.path.relpath(root, workspace_path)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1

            if depth > max_depth:
                continue

            indent = "  " * depth
            if rel_root != ".":
                lines.append(f"{indent}📁 {os.path.basename(root)}/")

            for file in sorted(files):
                ext = os.path.splitext(file)[1].lower()
                if ext in CODE_EXTENSIONS and not file.startswith("."):
                    total_files += 1
                    fp = Path(root) / file
                    symbols = extract_symbols_from_file(fp)
                    sym_desc = f" ({', '.join(symbols)})" if symbols else ""
                    lines.append(f"{indent}  📄 {file}{sym_desc}")

                if len("\n".join(lines)) > max_chars:
                    lines.append(f"{indent}  ... (kalan dosyalar özetlendi)")
                    break

            if len("\n".join(lines)) > max_chars:
                break
    except Exception as e:
        lines.append(f"(Harita tarama notu: {e})")

    result = "\n".join(lines)
    _cache[wp_str] = (now, result)
    return result
