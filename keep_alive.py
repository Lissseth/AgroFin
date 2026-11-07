"""
AgroFin - Keep-alive para Render (plan gratuito).

Render duerme el servicio tras ~15 minutos sin tráfico. Este script hace un
GET ligero al endpoint /health cada INTERVAL_SECONDS segundos para mantenerlo
despierto.

USO (solo cuando lo necesites):
    python keep_alive.py

Para detenerlo: Ctrl + C
"""

import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

URL = "https://agrofin-web.onrender.com/"
INTERVAL_SECONDS = 14 * 60  # 14 minutos (antes de los 15 de Render)


def ping():
    try:
        request = urllib.request.Request(URL, headers={"User-Agent": "AgroFin-KeepAlive/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:
        status = f"ERROR: {exc}"
    return status


def main():
    print("=" * 60)
    print(" AgroFin Keep-Alive - Render")
    print(f" URL      : {URL}")
    print(f" Intervalo: {INTERVAL_SECONDS // 60} minutos")
    print(" Pulsa Ctrl+C para detener.")
    print("=" * 60)

    while True:
        status = ping()
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Ping -> {status}"
        print(line, flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nKeep-alive detenido. Render volverá a dormirse en ~15 min.")
        sys.exit(0)
