"""Owned, loopback-only server for the vendored native Windows CAO engine."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time
from uuid import uuid4

import httpx
import psutil

from .processes import _WindowsJob, _record
from .local_media import create_media, media_scene_instructions
from .worker_catalog import MAX_WORKER_DEPTH, WorkerRequestError, get_worker, worker_prompt


class CAOError(RuntimeError):
    pass


def _pre_trust_workspace(workspace: str | Path) -> None:
    """Auto-trust workspace in ~/.codex/config.toml to avoid interactive modal delays."""
    try:
        ws_path = Path(workspace).resolve()
        codex_config = Path.home() / ".codex" / "config.toml"
        if not codex_config.is_file():
            return
        content = codex_config.read_text(encoding="utf-8")
        canonical = str(ws_path).lower()
        if f"[projects.'{canonical}']" in content or f'[projects."{canonical}"]' in content:
            return
        entry = f"\n[projects.'{canonical}']\ntrust_level = \"trusted\"\n"
        with codex_config.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


class CAOClient:
    """Run our checked-in CAO source without an external desktop application."""

    def __init__(self, root: Path, data_dir: Path):
        self.root = Path(root).resolve()
        self.source = self.root.parent / "vendor" / "cao-upstream"
        self.home = Path(data_dir).resolve() / "cao"
        self.python = self.source / ".venv" / "Scripts" / "python.exe"
        self._lock = asyncio.Lock()
        self._profile_lock = asyncio.Lock()
        self._process = None
        self._job = None
        self._log = None
        self._entry = None
        self._base = None
        self._token = None
        self._terminal_pool: dict[str, str] = {}

    def _installed(self) -> bool:
        return self.python.is_file() and (self.source / "src" / "cli_agent_orchestrator").is_dir()

    async def health(self) -> dict:
        if os.name != "nt":
            return {"available": False, "detail": "CAO Windows motoru yalnız native Windows üzerinde çalışır."}
        if not self._installed():
            return {"available": False, "detail": "Gömülü CAO kurulumu eksik. agent manager/setup.cmd çalıştırın."}
        if self._process is not None:
            if self._process.poll() is not None:
                return {"available": False, "detail": "CAO motoru kapandı; data/cao/server.log dosyasını inceleyin."}
            try:
                data = await self._probe()
                if data.get("terminal_backend") == "windows":
                    return {"available": True, "detail": "Gömülü CAO Windows motoru hazır."}
            except CAOError:
                return {"available": False, "detail": "CAO motoru başlatılıyor."}
        return {"available": True, "detail": "Gömülü CAO kurulu; ilk görevde başlatılacak."}

    async def _probe(self) -> dict:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=3.0) as client:
                response = await client.get(self._base + "/health", headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise CAOError("Gömülü CAO motoru yanıt vermiyor.") from exc
        if not isinstance(data, dict) or data.get("service") != "cli-agent-orchestrator" or data.get("terminal_backend") != "windows":
            raise CAOError("CAO servis kimliği veya Windows backend doğrulanamadı.")
        return data

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            return int(reservation.getsockname()[1])

    async def ensure_started(self) -> None:
        if os.name != "nt":
            raise CAOError("CAO Windows motoru native Windows gerektirir.")
        if not self._installed():
            raise CAOError("Gömülü CAO kurulu değil. agent manager/setup.cmd çalıştırın.")
        async with self._lock:
            if self._process is not None and self._process.poll() is None:
                await self._probe()
                return
            await self._close_locked()
            self.home.mkdir(parents=True, exist_ok=True)
            port = self._free_port()
            self._base = f"http://127.0.0.1:{port}"
            self._token = secrets.token_urlsafe(48)
            env = os.environ.copy()
            env["CAO_HOME_DIR"] = str(self.home)
            env["CAO_API_HOST"] = "127.0.0.1"
            env["CAO_API_PORT"] = str(port)
            env["CAO_TERMINAL_BACKEND"] = "windows"
            env["CAO_AUTH_LOCAL_TOKEN"] = self._token
            # Ensure agy.exe path is on PATH for CAO terminal subprocesses
            agy_dir = str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin")
            if agy_dir not in env.get("PATH", ""):
                env["PATH"] = agy_dir + os.pathsep + env.get("PATH", "")
            env["PYTHONPATH"] = str(self.source / "src") + os.pathsep + env.get("PYTHONPATH", "")
            for key in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI", "CAO_AUTH_AUDIENCE"):
                env.pop(key, None)
            self._log = (self.home / "server.log").open("ab", buffering=0)
            self._job = _WindowsJob()
            try:
                self._process = subprocess.Popen(
                    [str(self.python), "-m", "cli_agent_orchestrator.api.main",
                     "--host", "127.0.0.1", "--port", str(port), "--terminal", "windows"],
                    cwd=self.source, env=env, stdin=subprocess.DEVNULL,
                    stdout=self._log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW | 0x4,
                )
                self._job.attach_and_resume(self._process.pid)
                identity = psutil.Process(self._process.pid)
                self._entry = {
                    "resource_id": uuid4().hex, "owner_task": "agent-manager-cao",
                    "pid": self._process.pid,
                    "created_utc": datetime.fromtimestamp(identity.create_time(), timezone.utc).isoformat(),
                    "executable_path": identity.exe(), "parent_pid": os.getpid(),
                    "shared": False, "disposable": True, "state": "running",
                }
                _record(self.root, dict(self._entry))
            except Exception as exc:
                await self._close_locked()
                raise CAOError("CAO Windows motoru başlatılamadı.") from exc
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    await self._close_locked()
                    raise CAOError("CAO motoru açılırken kapandı; data/cao/server.log dosyasını inceleyin.")
                try:
                    await self._probe()
                    return
                except CAOError:
                    await asyncio.sleep(0.25)
            await self._close_locked()
            raise CAOError("CAO motoru 60 saniyede hazır olmadı; data/cao/server.log dosyasını inceleyin.")

    async def request(self, method: str, path: str, *, body=None, timeout: float = 15) -> dict:
        await self.ensure_started()
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
                response = await client.request(method, self._base + path, headers=self._headers(), json=body)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail_dict = {}
            try:
                raw_detail = exc.response.json().get("detail")
                if isinstance(raw_detail, dict):
                    detail_dict = raw_detail
                    detail = raw_detail.get("message") or raw_detail.get("kind")
                else:
                    detail = raw_detail
            except (ValueError, AttributeError):
                detail = None
            message = str(detail)[:350] if isinstance(detail, str) else "CAO çağrısı reddedildi."
            # Enrich error with the current terminal screen. The raw PTY log
            # repeats the entire prompt and can bury a quota line hundreds of
            # lines before its tail; the scrollback is the latest visible state.
            t_id = detail_dict.get("terminal_id")
            if not t_id and "terminal " in message.casefold():
                m = re.search(r"\bterminal\s+([a-zA-Z0-9_-]+)", message, re.IGNORECASE)
                if m:
                    t_id = m.group(1)
            if t_id and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(t_id)):
                try:
                    terminal_dir = self.home / "logs" / "terminal"
                    found_signal = False
                    for suffix in (".scrollback", ".log"):
                        terminal_file = terminal_dir / f"{t_id}{suffix}"
                        if not terminal_file.is_file():
                            continue
                        with terminal_file.open("rb") as stream:
                            stream.seek(max(0, terminal_file.stat().st_size - 128_000))
                            lines = stream.read().decode("utf-8", errors="replace").splitlines()
                        for line in reversed(lines[-100:]):
                            line_s = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line).strip()
                            if re.search(r"usage\s+limit|quota\s+(?:reached|exceeded|exhausted)|rate\s+limit|resets?\s+in|resource[_ -]?exhausted|try\s+again\s+at|too\s+many\s+requests", line_s, re.IGNORECASE):
                                message = f"{message} [{line_s[:250]}]"
                                found_signal = True
                                break
                        if found_signal:
                            break
                except Exception:
                    pass
            raise CAOError(f"CAO HTTP {exc.response.status_code}: {message}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise CAOError("CAO motoruyla iletişim kesildi.") from exc
        if not isinstance(data, dict):
            raise CAOError("CAO yanıtı beklenen biçimde değil.")
        return data

    async def _ensure_opencode_profile(self, profile: str) -> None:
        """Install the bundled CAO profile into this app's private OpenCode home."""
        if profile not in {"developer", "reviewer"}:
            raise CAOError("Bilinmeyen OpenCode ajan profili.")
        async with self._profile_lock:
            agent_file = self.home / "providers" / "opencode" / "agents" / f"{profile}.md"
            if agent_file.is_file():
                return
            self.home.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["CAO_HOME_DIR"] = str(self.home)
            env["PYTHONIOENCODING"] = "utf-8"
            command = [str(self.source / ".venv" / "Scripts" / "cao.exe"),
                       "install", profile, "--provider", "opencode_cli"]
            try:
                result = await asyncio.to_thread(
                    subprocess.run, command, cwd=self.source, env=env,
                    capture_output=True, text=True, encoding="utf-8", timeout=45,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CAOError(f"OpenCode {profile} profili kurulamadı.") from exc
            if result.returncode != 0 or not agent_file.is_file():
                raise CAOError(f"OpenCode {profile} profili kurulamadı: {result.stderr[-300:]}")

    async def run(self, model: dict, prompt: str, *, workspace: str, allow_writes: bool,
                  timeout: float, on_event, persona: str = "") -> dict:
        provider = {"codex": "codex", "antigravity": "antigravity_cli", "ollama": "ollama_local"}.get(model.get("provider"))
        if provider is None:
            raise CAOError("CAO seçeneğinde bu modelin CLI sağlayıcısı desteklenmiyor.")
        if provider == "codex":
            _pre_trust_workspace(workspace)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 200_000:
            raise CAOError("Görev metni boş veya çok büyük.")
        if provider == "ollama_local" and allow_writes:
            raise CAOError("Ollama CAO işçisi metin üretir; dosya yazma adımı için Codex veya Antigravity seçin.")
        await on_event("cao_starting", "CAO Windows işçisi hazırlanıyor.", {"provider": provider})
        job_id = uuid4().hex
        
        agent_role = persona.strip() if persona.strip() else ("developer" if allow_writes else "reviewer")
        # Respect the per-step limit chosen by the caller. A silently extended
        # one-hour limit made an undelivered prompt look like a running worker.
        effective_timeout = float(timeout)
        if effective_timeout <= 0:
            raise CAOError("CAO işçi süre sınırı pozitif olmalı.")

        # A terminal's stale IDLE state and composer echo are not completion evidence
        # for a new step. Use a fresh owned CLI process until turn IDs are available.
        pool_key = f"{provider}:{workspace}"
        reuse_id = None
        self._terminal_pool.pop(pool_key, None)

        body = {
            "provider": provider, "agent": agent_role,
            "prompt": prompt, "working_directory": workspace, "timeout": effective_timeout,
            "teardown": True, "job_id": job_id,
        }
        if reuse_id:
            body["reuse_terminal_id"] = reuse_id

        if provider == "ollama_local":
            body = {"model": model["id"], "prompt": prompt, "timeout": effective_timeout, "job_id": job_id}
            path = "/agent-manager/ollama/run-step"
        elif model.get("id") != "codex":
            # CLI catalog IDs include the effort variant (for example -high).
            # base_model drops that suffix and silently selects the CLI default.
            body["model"] = model["id"] if provider == "antigravity_cli" else (model.get("base_model") or model["id"])
            path = "/terminals/run-step"
        else:
            path = "/terminals/run-step"
        async def report_terminal():
            while True:
                try:
                    active = await self.request("GET", f"/agent-manager/jobs/{job_id}", timeout=3)
                    terminal_id = active.get("terminal_id")
                    if isinstance(terminal_id, str) and re.fullmatch(r"[0-9a-f]{8,32}", terminal_id):
                        await on_event("cao_terminal_ready", "Çalışan terminal bağlantısı hazır.",
                                       {"terminal_id": terminal_id})
                        return
                except CAOError:
                    pass
                await asyncio.sleep(1)
        terminal_watch = asyncio.create_task(report_terminal()) if provider != "ollama_local" else None
        try:
            data = await self.request("POST", path, body=body, timeout=effective_timeout + 300)
        except CAOError as exc:
            if reuse_id:
                # The pooled terminal session may have died; retry once with a fresh process
                self._terminal_pool.pop(pool_key, None)
                body.pop("reuse_terminal_id", None)
                data = await self.request("POST", path, body=body, timeout=effective_timeout + 300)
            else:
                raise
        except asyncio.CancelledError:
            if self._base and self._token:
                try:
                    async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
                        await client.delete(
                            self._base + f"/agent-manager/jobs/{job_id}",
                            headers=self._headers(),
                        )
                except httpx.HTTPError:
                    pass
            raise
        finally:
            if terminal_watch:
                terminal_watch.cancel()
                await asyncio.gather(terminal_watch, return_exceptions=True)
        output = data.get("last_message")
        if not isinstance(output, str) or not output.strip():
            raise CAOError("CAO işçisi kullanılabilir bir yanıt döndürmedi.")
        if output.startswith("[NO RESPONSE - agent completed without producing a text response"):
            raise CAOError("CAO işçisi metin yanıtı üretmedi; terminal çıktısı yanıt olarak kabul edilmedi.")
        if re.search(r"\[Pasted Content \d+ chars\]", output) or output.strip() == prompt.strip():
            raise CAOError("CAO terminali ajan yanıtı yerine gönderilen promptun yankısını döndürdü; adım tamamlanmadı.")
        output_lower = output.lower()
        if any(term in output_lower for term in ("you’ve hit your usage limit", "you've hit your usage limit", "usage limit", "quota_exceeded", "insufficient_quota", "too many requests", "rate limit reached")):
            raise CAOError(f"Model ({model.get('id')}) kullanım kotasına ulaşıldı: {output[:300]}")
        terminal_id = data.get("terminal_id")
        await on_event("cao_completed", "CAO işçisi tamamladı.", {"terminal_id": terminal_id, "warm_session": bool(reuse_id)})
        return {"output": output, "model_id": model["id"], "status": "completed",
                "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
                "cao_terminal_id": terminal_id}

    async def run_worker(self, role: str, task: str, model: dict, *, workspace: str,
                         allow_writes: bool, timeout: float, on_event,
                         depth: int = 0) -> dict:
        """Run one bounded specialist through the existing CAO provider route.

        Only the parent agent may delegate. A child receives no delegation tool,
        cannot request another child, and never gains broader write permission.
        """
        if depth < 0 or depth >= MAX_WORKER_DEPTH:
            raise CAOError("CAO işçileri başka işçi çağıramaz.")
        try:
            spec = get_worker(role)
            worker_writes = bool(allow_writes and spec.may_write)
            if role in {"image_generator", "video_generator"} and not allow_writes:
                raise WorkerRequestError("Medya dosyası üretmek için dosya yazma izni gerekli.")
            prompt = (media_scene_instructions(role, task)
                      if role in {"image_generator", "video_generator"}
                      else worker_prompt(spec, task, allow_writes=worker_writes))
        except WorkerRequestError as exc:
            raise CAOError(str(exc)) from exc
        if worker_writes and role not in {"image_generator", "video_generator"} and model.get("provider") == "ollama":
            raise CAOError("Ollama kod işçisi dosya düzenleyemez; yazabilen model seçin.")
        bounded_timeout = float(timeout)
        if bounded_timeout <= 0:
            raise CAOError("İşçi süre sınırı pozitif olmalı.")
        await on_event("cao_worker_started", f"{spec.title} işçisi çağrıldı.",
                       {"role": role, "model_id": model.get("id"), "depth": depth + 1})
        try:
            result = await self.run(model, prompt, workspace=workspace,
                                    allow_writes=False if role in {"image_generator", "video_generator"} else worker_writes,
                                    timeout=bounded_timeout,
                                    on_event=on_event,
                                    persona="developer" if worker_writes and role not in {"image_generator", "video_generator"} else "reviewer")
            if role in {"image_generator", "video_generator"}:
                artifact = await create_media(role, result["output"], workspace,
                                              on_event=on_event)
                result["artifact"] = artifact
                result["output"] = f"{spec.title} tamamlandı: {artifact['path']}"
            if "<cao_workers>" in result["output"]:
                raise CAOError("Alt işçi başka işçi çağırmaya çalıştı; iç içe çağrı reddedildi.")
        except Exception as exc:
            await on_event("cao_worker_failed", f"{spec.title} işçisi tamamlanamadı.",
                           {"role": role, "error": type(exc).__name__})
            raise
        result["worker_role"] = role
        result["worker_output_kind"] = spec.output_kind
        await on_event("cao_worker_completed", f"{spec.title} işçisi tamamladı.",
                       {"role": role, "model_id": result.get("model_id")})
        return result

    async def clear_terminal_pool(self, workspace: str | None = None) -> None:
        """Tear down pooled ConPTY terminals for a specific workspace or all."""
        keys = list(self._terminal_pool.keys())
        for key in keys:
            if workspace is None or key.endswith(f":{workspace}"):
                tid = self._terminal_pool.pop(key, None)
                if tid and self._base and self._token:
                    try:
                        await self.request("DELETE", f"/terminals/{tid}", timeout=5.0)
                    except Exception:
                        pass

    async def _close_locked(self) -> None:
        await self.clear_terminal_pool()
        process, job, log, entry = self._process, self._job, self._log, self._entry
        self._process = self._job = self._log = self._entry = None
        self._base = self._token = None
        if job is not None:
            job.close()
        if process is not None:
            try:
                await asyncio.to_thread(process.wait, 5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
        if log is not None:
            log.close()
        if entry is not None:
            entry["state"] = "completed"
            _record(self.root, dict(entry))

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()
