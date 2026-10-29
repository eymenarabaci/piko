"""Persona management, disk scanning, creation, and CAO profile synchronization."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


class PersonaManager:
    """Discovers pre-existing personas, dynamically creates new ones, and syncs with CAO."""

    def __init__(self, workspace_root: Path, cao_home: Path):
        self.workspace_root = Path(workspace_root)
        self.cao_home = Path(cao_home)
        self.skills_dir = self.workspace_root / "skills"
        self.agents_dir = self.cao_home / "providers" / "opencode" / "agents"
        self.cao_agent_store_dir = self.cao_home / "agent-store"
        self.custom_personas_dir = self.cao_home / "custom_personas"

    def ensure_directories(self) -> None:
        for d in (self.agents_dir, self.cao_agent_store_dir, self.custom_personas_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    def list_skills(self) -> list[dict[str, str]]:
        """List built-in skills available in the project, including vendor skills."""
        skills = []
        if not self.skills_dir.is_dir():
            return skills

        # 1. Base skills
        for skill_path in self.skills_dir.iterdir():
            if skill_path.is_dir() and skill_path.name != "vendor":
                readme = skill_path / "SKILL.md"
                desc = self._get_skill_desc(readme)
                skills.append({"name": skill_path.name, "description": desc})

        # 2. Vendor skills
        vendor_dir = self.skills_dir / "vendor"
        if vendor_dir.is_dir():
            for v_path in vendor_dir.iterdir():
                if v_path.is_dir():
                    readme = v_path / "SKILL.md"
                    clean_name = v_path.name.replace("vendor-", "")
                    clean_name = clean_name.split("-")[0] if "-" in clean_name else clean_name
                    # keep meaningful tag
                    tag_name = v_path.name.replace("vendor-", "")
                    # remove trailing hash if any
                    parts = tag_name.split("-")
                    if len(parts) > 1 and len(parts[-1]) == 8:
                        tag_name = "-".join(parts[:-1])
                    desc = self._get_skill_desc(readme)
                    skills.append({"name": tag_name, "description": desc})

        return skills

    def list_mcps(self) -> list[dict[str, str]]:
        """List supported MCP servers configured in orchestrator catalog."""
        try:
            from orchestrator.mcp_catalog import SPECS
            return [{"id": name, "capabilities": spec[0]} for name, spec in SPECS.items()]
        except Exception:
            return [
                {"id": "firebase", "capabilities": "firebase cloud backend"},
                {"id": "github", "capabilities": "git github repository"},
                {"id": "playwright", "capabilities": "browser testing frontend"},
                {"id": "sqlite", "capabilities": "database sqlite analysis"},
                {"id": "context7", "capabilities": "documentation research"},
            ]

    def _get_skill_desc(self, readme_path: Path) -> str:
        if readme_path.is_file():
            try:
                lines = readme_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[:8]:
                    line_s = line.strip()
                    if line_s and not line_s.startswith("#") and not line_s.startswith("---"):
                        return line_s[:120]
            except Exception:
                pass
        return ""

    def list_personas(self) -> list[dict[str, Any]]:
        """List all pre-existing personas from CAO agent-store, OpenCode agents, and local personas folder."""
        self.ensure_directories()
        personas = {}

        # 1. Native CAO agent-store (primary source of truth for CAO)
        if self.cao_agent_store_dir.is_dir():
            for agent_file in self.cao_agent_store_dir.glob("*.md"):
                name = agent_file.stem
                personas[name] = {
                    "name": name,
                    "source": "cao_agent_store",
                    "file_path": str(agent_file),
                    "instructions": self._read_head(agent_file),
                }

        # 2. CAO providers / OpenCode agents
        if self.agents_dir.is_dir():
            for agent_file in self.agents_dir.glob("*.md"):
                name = agent_file.stem
                if name not in personas:
                    personas[name] = {
                        "name": name,
                        "source": "cao_agent",
                        "file_path": str(agent_file),
                        "instructions": self._read_head(agent_file),
                    }

        # 3. Local custom personas
        if self.custom_personas_dir.is_dir():
            for agent_file in self.custom_personas_dir.glob("*.md"):
                name = agent_file.stem
                if name not in personas:
                    personas[name] = {
                        "name": name,
                        "source": "custom_persona",
                        "file_path": str(agent_file),
                        "instructions": self._read_head(agent_file),
                    }

        # 4. Workspace data/personas folder if present
        workspace_personas_dir = self.workspace_root / "data" / "personas"
        if workspace_personas_dir.is_dir():
            for agent_file in workspace_personas_dir.glob("*.md"):
                name = agent_file.stem
                if name not in personas:
                    personas[name] = {
                        "name": name,
                        "source": "workspace_persona",
                        "file_path": str(agent_file),
                        "instructions": self._read_head(agent_file),
                    }

        # Default fallback personas if empty
        if not personas:
            personas["developer"] = {
                "name": "developer",
                "source": "default",
                "instructions": "Full workspace-write developer for code implementation.",
            }
            personas["reviewer"] = {
                "name": "reviewer",
                "source": "default",
                "instructions": "Read-only auditor and code reviewer.",
            }

        return list(personas.values())

    def get_persona(self, name: str) -> dict[str, Any] | None:
        all_p = {p["name"]: p for p in self.list_personas()}
        return all_p.get(name)

    def ensure_persona_in_cao(
        self, name: str, instructions: str = "", skills: list[str] | None = None, is_writer: bool = True
    ) -> dict[str, Any]:
        """Guarantee that a valid persona profile with frontmatter exists in CAO's agent-store."""
        self.ensure_directories()
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name).strip().lower()
        if not safe_name:
            safe_name = "developer"

        cao_file = self.cao_agent_store_dir / f"{safe_name}.md"
        if cao_file.is_file():
            try:
                text = cao_file.read_text(encoding="utf-8")
                if text.strip().startswith("---"):
                    return {"name": safe_name, "file_path": str(cao_file)}
            except Exception:
                pass

        # Check existing candidates that might have valid frontmatter or instructions
        candidate_instructions = instructions
        for candidate_dir in (self.custom_personas_dir, self.agents_dir, self.workspace_root / "data" / "personas"):
            cand = candidate_dir / f"{safe_name}.md"
            if cand.is_file():
                try:
                    text = cand.read_text(encoding="utf-8")
                    if text.strip().startswith("---"):
                        cao_file.write_text(text, encoding="utf-8")
                        return {"name": safe_name, "file_path": str(cao_file)}
                    elif not candidate_instructions:
                        candidate_instructions = text
                except Exception:
                    pass

        return self.create_or_update_persona(
            name=safe_name,
            instructions=candidate_instructions or f"{safe_name} için görev uzmanı.",
            skills=skills or [],
            is_writer=is_writer,
        )

    def create_or_update_persona(
        self, name: str, instructions: str, skills: list[str] | None = None, is_writer: bool = True
    ) -> dict[str, Any]:
        """Create a persona file with valid YAML frontmatter and mirror it into CAO agent-store."""
        self.ensure_directories()
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name).strip().lower()
        if not safe_name:
            safe_name = "custom_agent"

        skill_block = ""
        skill_tags = []
        if skills:
            skill_block = f"\n\n### İlgili Beceriler (Skills)\n" + "\n".join(f"- {s}" for s in skills)
            skill_tags = [f"  - {s}" for s in skills]

        frontmatter = [
            "---",
            f"name: {safe_name}",
            f"description: Specialist persona for {safe_name}",
            f"role: {'developer' if is_writer else 'reviewer'}",
            "tags:",
            f"  - {safe_name}",
            *(skill_tags if skill_tags else ["  - general"]),
            "capabilities:",
            f"  - execute tasks as {safe_name}",
            "---",
            "",
        ]

        content = "\n".join(frontmatter) + (
            f"# PERSONA: {safe_name.upper()}\n\n"
            f"**Rol:** {'Dosya Düzenleyen Geliştirici (Write)' if is_writer else 'Salt Okunur Denetçi (Read-only)'}\n\n"
            f"## Görev ve Sistem Talimatları\n{instructions.strip()}{skill_block}\n"
        )

        # 1. Write to custom personas store
        custom_file = self.custom_personas_dir / f"{safe_name}.md"
        custom_file.write_text(content, encoding="utf-8")

        # 2. Mirror into CAO's LOCAL_AGENT_STORE_DIR (data/cao/agent-store) where CAO looks!
        cao_store_file = self.cao_agent_store_dir / f"{safe_name}.md"
        cao_store_file.write_text(content, encoding="utf-8")

        # 3. Also mirror into OpenCode agents directory
        cao_opencode_file = self.agents_dir / f"{safe_name}.md"
        cao_opencode_file.write_text(content, encoding="utf-8")

        return {
            "name": safe_name,
            "source": "custom_created",
            "file_path": str(cao_store_file),
            "instructions": instructions,
        }

    @staticmethod
    def _read_head(path: Path, max_chars: int = 500) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        except Exception:
            return ""
