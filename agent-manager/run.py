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
    parser = argparse.ArgumentParser(description="Agent Manager yerel sunucu")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Arayüzü varsayılan tarayıcıda aç")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Dinlenecek IP adresi")
    parser.add_argument("--remote", action="store_true", help="Tailscale ve uzaktan mobil erişimi etkinleştir (0.0.0.0)")
    args = parser.parse_args()
    # Reuse an existing instance rather than launching a second engine on its port.
    try:
        with socket.create_connection(("127.0.0.1", args.port), timeout=1):
            occupied = True
    except OSError:
        occupied = False
    if occupied:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{args.port}/openapi.json", timeout=3) as response:
                existing = json.load(response)
            if existing.get("info", {}).get("title") != "Agent Manager":
                raise ValueError("Different service")
        except Exception:
            print(f"Port {args.port} baska bir uygulama tarafindan kullaniliyor. Agent Manager baslatilmadi.")
            raise SystemExit(1)
        print(f"Agent Manager zaten acik: http://127.0.0.1:{args.port}")
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

    listen_host = "0.0.0.0" if args.remote else args.host
    config = uvicorn.Config("manager.app:app", host=listen_host, port=args.port, loop="none")
    process = psutil.Process()
    root = Path(__file__).resolve().parent

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
        print(" [Agent Manager] Uzaktan ve Mobil Erisim Modu AKTIF")
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
        asyncio.run(uvicorn.Server(config).serve())
    finally:
        if opener:
            opener.cancel()
        entry["state"] = "completed"
        _record(root, entry)


if __name__ == "__main__":
    main()
