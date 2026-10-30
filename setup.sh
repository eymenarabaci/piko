#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/agent-manager"

echo "============================================================"
echo "  Piko: Linux / macOS Kurulumu"
echo "============================================================"

if ! command -v python3 &> /dev/null; then
    echo "[HATA] Python 3 bulunamadı. Lütfen Python kurun."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Python sanal ortamı (.venv) oluşturuluyor..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
if [ -f "requirements.lock.txt" ]; then
    pip install -r requirements.lock.txt
else
    pip install -e .
fi

echo "============================================================"
echo "  Kurulum Tamamlandı!"
echo "  Başlatmak için: python run.py --open"
echo "============================================================"
