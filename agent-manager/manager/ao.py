"""AO v0.14.6 loopback client. LangGraph remains the workflow authority."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote

import httpx


class AOError(RuntimeError):
    pass


class AOClient:
    def __init__(self, run_file: Path | None = None, transport=None):
        self.run_file = Path(run_file or os.environ.get("AO_RUN_FILE", Path.home() / ".ao" / "running.json"))
        self.transport = transport

    async def _endpoint(self) -> str:
        try:
            info = json.loads(self.run_file.read_text(encoding="utf-8"))
            port, pid = int(info["port"]), int(info["pid"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AOError("AO daemon çalışmıyor. Orchestrator masaüstü uygulamasını açın.") from exc
        if not 1 <= port <= 65535 or pid <= 0:
            raise AOError("AO daemon kimlik dosyası geçersiz.")
        base = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, timeout=5) as client:
                response = await client.get(base + "/readyz")
                response.raise_for_status()
                probe = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AOError("AO daemon yanıt vermiyor. Orchestrator uygulamasını açın.") from exc
        if probe.get("service") != "agent-orchestrator-daemon" or probe.get("pid") != pid or probe.get("status") != "ready":
            raise AOError("AO daemon kimliği doğrulanamadı.")
        return base

    async def request(self, method: str, path: str, body=None) -> dict:
        base = await self._endpoint()
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, timeout=15) as client:
                response = await client.request(method, base + path, json=body)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                payload = exc.response.json()
                error = payload.get("error", {})
                code = payload.get("code", "") or (error.get("code", "") if isinstance(error, dict) else "")
                message = payload.get("message", "") or (error.get("message", "") if isinstance(error, dict) else "")
            except ValueError:
                code = ""
                message = ""
            reason = ": ".join(part for part in (code, message) if part)
            raise AOError(f"AO isteği reddetti ({exc.response.status_code}{': ' + reason if reason else ''}).") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AOError("AO daemon isteği tamamlanamadı.") from exc
        if not isinstance(data, dict):
            raise AOError("AO daemon yanıt biçimi geçersiz.")
        return data

    async def health(self) -> dict:
        try:
            await self._endpoint()
            return {"available": True, "detail": "AO daemon hazır"}
        except AOError as exc:
            return {"available": False, "detail": str(exc)}

    @staticmethod
    def _git_root(workspace: str) -> str:
        completed = subprocess.run(["git", "-C", workspace, "rev-parse", "--show-toplevel"],
                                   capture_output=True, text=True, timeout=5, check=False)
        if completed.returncode:
            raise AOError("AO yürütücüsü için Git deposu seçin; worktree yalıtımı gereklidir.")
        return str(Path(completed.stdout.strip()).resolve())

    async def ensure_project(self, workspace: str) -> str:
        root = await asyncio.to_thread(self._git_root, workspace)
        projects = (await self.request("GET", "/api/v1/projects")).get("projects", [])
        for project in projects:
            if project.get("kind") == "single_repo" and os.path.normcase(os.path.normpath(project.get("path", ""))) == os.path.normcase(os.path.normpath(root)):
                return project["id"]
        created = await self.request("POST", "/api/v1/projects", {"path": root})
        project_id = created.get("project", {}).get("id")
        if not project_id:
            raise AOError("AO proje kaydı yanıtı geçersiz.")
        return project_id

    async def kill(self, session_id: str) -> None:
        await self.request("POST", f"/api/v1/sessions/{quote(session_id, safe='')}/kill", {})

    async def run(self, model: dict, prompt: str, *, workspace: str, allow_writes: bool,
                  timeout: float, request_id: str, session_id: str | None,
                  on_spawn, on_event) -> dict:
        provider = model.get("provider")
        if provider != "codex":
            raise AOError("AO yürütücüsünde şu anda Codex kullanın; Antigravity için doğrudan CLI yürütücüsünü seçin.")
        if not allow_writes:
            raise AOError("AO işçileri ayrı worktree'de çalışır; AO için dosya yazma iznini açın.")
        if len(prompt.encode("utf-8")) > 16000:
            raise AOError("AO görev metni 16 KiB sınırını aşıyor.")
        project_id = await self.ensure_project(workspace)
        harness = "codex"
        if not session_id:
            # AO model discovery can reject an explicit override even when the CLI accepts it.
            # The generic Codex catalog identity deliberately uses AO's signed-in default.
            override = model["id"] if model["id"] != "codex" else ""
            task_prompt = f"Piko görev kimliği: {request_id}\n\n{prompt}"
            if len(task_prompt.encode("utf-8")) > 16384:
                raise AOError("AO görev metni 16 KiB sınırını aşıyor.")
            body = {"clientRequestId": request_id, "projectId": project_id, "kind": "worker",
                    "harness": harness, "mode": "chat",
                    "approvalMode": "accept-edits", "prompt": task_prompt,
                    "displayName": f"Piko {request_id[-12:]}"}
            if override:
                body["model"] = override
            spawned = await self.request("POST", "/api/v1/sessions", body)
            session_id = spawned.get("session", {}).get("id")
            if not session_id:
                raise AOError("AO oturum kimliği döndürmedi.")
            await on_spawn(session_id)
            await on_event("ao_spawned", "AO işçisi açıldı.", {"ao_session_id": session_id, "harness": harness})
        target = f"/api/v1/sessions/{quote(session_id, safe='')}"
        async with asyncio.timeout(timeout):
            while True:
                session = (await self.request("GET", target)).get("session", {})
                if session.get("isTerminated"):
                    raise AOError(f"AO oturumu sonlandı: {session_id}")
                activity = session.get("activity", {})
                state = activity.get("state") if isinstance(activity, dict) else None
                if state in {"blocked", "waiting_input"} or session.get("status") == "needs_input":
                    raise AOError(f"AO işçisi kullanıcı girdisi veya izin bekliyor: {session_id}")
                history = (await self.request("GET", target + "/history?limit=1")).get("turns", [])
                if (state == "idle" and history and request_id in history[0].get("userRequest", "")
                        and isinstance(history[0].get("agentResponse"), str) and history[0]["agentResponse"].strip()):
                    output = history[0]["agentResponse"]
                    return {"output": output, "model_id": model["id"], "status": "completed",
                            "usage": {}, "ao_session_id": session_id, "ao_project_id": project_id}
                if state == "exited":
                    raise AOError(f"AO işçisi çıktı vermeden kapandı: {session_id}")
                await asyncio.sleep(2)
