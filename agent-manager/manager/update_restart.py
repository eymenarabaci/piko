"""Detached one-shot helper: restart the same local server after an update."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _record(path: Path, state: str, detail: str = "") -> None:
    path.write_text(json.dumps({"state": state, "detail": detail,
                                "at": datetime.now(timezone.utc).isoformat()},
                               ensure_ascii=False), encoding="utf-8")


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _new_server_ready(port: int, old_started_at: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/openapi.json", timeout=2) as response:
            if json.load(response).get("info", {}).get("title") != "Agent Manager":
                return False
        with opener.open(f"http://127.0.0.1:{port}/api/update/status", timeout=3) as response:
            return json.load(response).get("server_started_at") != old_started_at
    except (OSError, ValueError):
        return False


def main() -> int:
    if len(sys.argv) != 9:
        return 2
    _, parent_pid, parent_created, port_value, executable, root_value, args_json, status_value, old_started_at = sys.argv
    root = Path(root_value).resolve()
    status = Path(status_value).resolve()
    port = int(port_value)
    args = json.loads(args_json)
    if not isinstance(args, list) or any(not isinstance(x, str) for x in args):
        return 2
    try:
        import psutil
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                process = psutil.Process(int(parent_pid))
                still_running = abs(process.create_time() - float(parent_created)) < 0.01
            except psutil.NoSuchProcess:
                still_running = False
            if not still_running and _port_free(port):
                break
            time.sleep(0.5)
        else:
            _record(status, "failed", "Eski sunucu kapanmadı veya bağlantı noktası boşalmadı.")
            return 1
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        setup = root.parent / "setup.cmd"
        dependency_issue = ""
        if os.name == "nt" and setup.is_file():
            try:
                installed = subprocess.run(["cmd.exe", "/d", "/c", str(setup)],
                                           cwd=root.parent, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=flags, timeout=360)
            except (OSError, subprocess.TimeoutExpired):
                dependency_issue = "Güncelleme bağımlılıkları kurulamadı; mevcut ortamla başlatıldı."
            else:
                if installed.returncode:
                    dependency_issue = "Güncelleme bağımlılıkları kurulamadı; mevcut ortamla başlatıldı."
        subprocess.Popen([executable, str(root / "run.py"), *args], cwd=root,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags,
                         close_fds=True)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if _new_server_ready(port, old_started_at):
                _record(status, "dependencies_failed" if dependency_issue else "ready", dependency_issue)
                return 0
            time.sleep(0.5)
        _record(status, "failed", "Yeni sunucu bağlantı noktasında başlamadı.")
        return 1
    except Exception:
        _record(status, "failed", "Otomatik yeniden başlatma başarısız oldu.")
        return 1
    finally:
        try:
            Path(__file__).unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
