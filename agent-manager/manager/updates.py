"""Conservative GitHub fast-forward updates for a local Piko checkout."""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class UpdateError(RuntimeError):
    pass


class Updater:
    def __init__(self, repo: Path, data_dir: Path):
        self.repo = Path(repo).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.lock = asyncio.Lock()

    def _git(self, *args: str, timeout: int = 30) -> str:
        executable = shutil.which("git")
        if not executable:
            raise UpdateError("Git bulunamadı.")
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "Never"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.run([executable, *args], cwd=self.repo, env=env,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UpdateError("Git işlemi başlatılamadı veya zaman aşımına uğradı.") from exc
        if proc.returncode:
            # Git stderr can contain credential-bearing remote URLs. Do not expose it.
            raise UpdateError("Git işlemi başarısız oldu; uzak depo bağlantısını ve yerel değişiklikleri kontrol edin.")
        return proc.stdout.strip()

    def _repo_name(self) -> str:
        if os.path.normcase(os.path.abspath(self._git("rev-parse", "--show-toplevel"))) != os.path.normcase(str(self.repo)):
            raise UpdateError("Uygulama beklenen Git depo kökünde çalışmıyor.")
        if self._git("branch", "--show-current") != "main":
            raise UpdateError("Güncelleme yalnız main dalında yapılabilir.")
        url = self._git("remote", "get-url", "origin")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password:
            raise UpdateError("Origin yalnız kimlik bilgisi içermeyen GitHub HTTPS adresi olabilir.")
        parts = parsed.path.removesuffix(".git").strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            raise UpdateError("Origin GitHub depo adresi geçersiz.")
        return "/".join(parts)

    def _status(self, refresh: bool) -> dict:
        try:
            repo_name = self._repo_name()
            if refresh:
                self._git("fetch", "--quiet", "--no-tags", "origin",
                          "refs/heads/main:refs/remotes/origin/main", timeout=60)
            current = self._git("rev-parse", "HEAD")
            remote = self._git("rev-parse", "refs/remotes/origin/main")
            left, right = self._git("rev-list", "--left-right", "--count",
                                    "HEAD...refs/remotes/origin/main").split()
            ahead, behind = int(left), int(right)
            dirty = bool(self._git("status", "--porcelain", "--untracked-files=all"))
            reason = ""
            if dirty:
                reason = "Yerel kod değişiklikleri var; önce bunları commit edin veya taşıyın."
            elif ahead:
                reason = "Yerel main dalı GitHub dalından ayrılmış; otomatik birleştirme yapılmaz."
            elif not behind:
                reason = "Zaten güncel."
            return {"repo": repo_name, "current": current, "remote": remote,
                    "behind": behind, "ahead": ahead, "dirty": dirty,
                    "eligible": bool(behind and not ahead and not dirty),
                    "reason": reason, "restart_required": False}
        except (UpdateError, ValueError, OSError) as exc:
            return {"repo": "", "current": "", "remote": "", "behind": 0,
                    "ahead": 0, "dirty": False, "eligible": False,
                    "reason": str(exc) if isinstance(exc, UpdateError) else "Git durumu okunamadı.",
                    "restart_required": False}

    async def status(self, refresh: bool = False) -> dict:
        async with self.lock:
            return await asyncio.to_thread(self._status, refresh)

    def _backup_data(self) -> Path:
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / f"before-update-{stamp}-{uuid.uuid4().hex[:8]}.zip"
        temporary = backup_dir / f".{target.name}.partial"
        try:
            with tempfile.TemporaryDirectory(dir=backup_dir) as scratch, zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                allowZip64=True
            ) as archive:
                for source in self.data_dir.rglob("*"):
                    if not source.is_file() or source.is_symlink():
                        continue
                    relative = source.relative_to(self.data_dir)
                    if relative.parts[0] == "backups" or "logs" in relative.parts:
                        continue
                    if source.suffix.lower() in {".sqlite-wal", ".sqlite-shm", ".sqlite-journal"}:
                        continue
                    if source.suffix.lower() == ".sqlite":
                        snapshot = Path(scratch) / (source.stem + ".sqlite")
                        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as live:
                            with closing(sqlite3.connect(snapshot)) as saved:
                                live.backup(saved)
                        archive.write(snapshot, relative.as_posix())
                    else:
                        archive.write(source, relative.as_posix())
            temporary.replace(target)
            return target
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise UpdateError("Veri yedeği oluşturulamadı; güncelleme uygulanmadı.") from exc

    def _apply(self) -> dict:
        status = self._status(refresh=True)
        if not status["eligible"]:
            raise UpdateError(status["reason"] or "Güncelleme uygulanamaz.")
        self._backup_data()
        # Another process may have touched the checkout during the backup.
        if self._git("status", "--porcelain", "--untracked-files=all"):
            raise UpdateError("Yedekleme sırasında yerel kod değişti; güncelleme uygulanmadı.")
        self._git("merge", "--ff-only", status["remote"], timeout=60)
        return {"status": "updated", "repo": status["repo"],
                "current": status["remote"], "remote": status["remote"],
                "behind": 0, "eligible": False, "dirty": False,
                "reason": "Yeniden başlatılıyor.", "restart_required": True}

    async def apply(self) -> dict:
        async with self.lock:
            return await asyncio.to_thread(self._apply)
