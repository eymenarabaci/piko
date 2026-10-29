"""Project management module for Agent Manager.
Organizes runs, workspaces, and chat sessions by project, similar to Codex/Claude Projects.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class ProjectManager:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.file_path = self.data_dir / "projects.json"
        self._ensure_file()

    def _ensure_file(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            default_projects = [
                {
                    "id": "proj-is-gelistirme",
                    "name": "İş Geliştirme Projesi",
                    "path": r"./workspace",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "active": True
                }
            ]
            self.file_path.write_text(json.dumps(default_projects, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> list[dict]:
        self._ensure_file()
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def get(self, project_id: str) -> Optional[dict]:
        for p in self.list():
            if p.get("id") == project_id:
                return p
        return None

    def create(self, name: str, path: str) -> dict:
        projects = self.list()
        proj_id = "proj-" + uuid.uuid4().hex[:8]
        new_proj = {
            "id": proj_id,
            "name": name.strip(),
            "path": path.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True
        }
        projects.insert(0, new_proj)
        self.file_path.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
        return new_proj

    def delete(self, project_id: str) -> bool:
        projects = self.list()
        filtered = [p for p in projects if p.get("id") != project_id]
        if len(filtered) != len(projects):
            self.file_path.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        return False
