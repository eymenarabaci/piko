"""Use a Proactor loop on Windows so async CLI processes work under Uvicorn."""
import argparse
import asyncio
import os
import sys
import threading
import webbrowser
import json
import socket
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

import uvicorn
import psutil

from manager.processes import _record


def main():
    parser = argparse.ArgumentParser(description="Piko yerel sunucu")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Arayüzü varsayılan tarayıcıda aç")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Dinlenecek IP adresi")
    parser.add_argument("--remote", action="store_true", help="Tailscale ve uzaktan mobil erişimi etkinleştir (0.0.0.0)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # A second checkout must never silently open the first checkout's UI.
    requested_port = args.port
    for candidate in range(requested_port, requested_port + 11):
        try:
            with socket.create_connection(("127.0.0.1", candidate), timeout=1):
                occupied = True
        except OSError:
            occupied = False
        if not occupied:
            args.port = candidate
            if candidate != requested_port:
                print(f"Port {requested_port} dolu; bu kopya {candidate} portunda aciliyor.")
            break
        try:
            with opener.open(f"http://127.0.0.1:{candidate}/api/bootstrap", timeout=3) as response:
                existing = json.load(response)
            existing_root = Path(existing.get("workspace_default", "")).resolve()
            same_checkout = existing_root == root
        except Exception:
            same_checkout = False
        if same_checkout:
            print(f"Piko zaten acik: http://127.0.0.1:{candidate}")
            if args.open:
                webbrowser.open(f"http://127.0.0.1:{candidate}")
            return
    else:
        print(f"{requested_port}-{requested_port + 10} portlari dolu. Piko baslatilmadi.")
        raise SystemExit(1)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

    listen_host = "0.0.0.0" if args.remote else args.host
    config = uvicorn.Config("manager.app:app", host=listen_host, port=args.port, loop="none")
    server = uvicorn.Server(config)
    from manager.app import app as asgi_app
    asgi_app.state.uvicorn_server = server
    asgi_app.state.restart_executable = sys.executable
    asgi_app.state.restart_args = (["--remote"] if args.remote else ["--host", args.host]) + ["--port", str(args.port)]
    asgi_app.state.restart_port = args.port
    process = psutil.Process()

    token_path = root / "data" / "remote_token.txt"
    token_str = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else "(Henüz oluşturulmadı - sunucu ilk açılışta oluşturacak)"

    if args.remote or listen_host != "127.0.0.1":
        detected_ip = "127.0.0.1"
        try:
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family.name == "AF_INET" and not addr.address.startswith("127.") and not addr.address.startswith("169.254."):
                        if not addr.address.startswith("26."):  # Radmin harici
                            detected_ip = addr.address
                            break
        except Exception:
            pass

        print("\n" + "=" * 64)
        print(" [Piko] Uzaktan ve Mobil Erisim Modu AKTIF")
        print(f" Telefondan Baglanti Adresi (Wi-Fi): http://{detected_ip}:{args.port}")
        print(f" Mobil Token: {token_str}")
        print(" Android APK'sinda bu adresi ve token'i girerek baglanabilirsiniz.")
        print("=" * 64 + "\n")

    entry = {"resource_id": f"server-{os.getpid()}-{process.create_time()}",
             "owner_task": "agent-manager-server", "pid": process.pid,
             "created_utc": datetime.fromtimestamp(process.create_time(), timezone.utc).isoformat(),
             "executable_path": process.exe(), "parent_pid": process.ppid(),
             "shared": False, "disposable": True, "state": "running"}
    _record(root, entry)
    opener = None
    if args.open:
        opener = threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}"))
        opener.daemon = True
        opener.start()
    try:
        asyncio.run(server.serve())
    finally:
        if opener:
            opener.cancel()
        entry["state"] = "completed"
        _record(root, entry)


if __name__ == "__main__":
    main()
