"""启动入口：python run.py [--port 8765] [--no-browser]"""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="AI 网文工作台")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not args.no_browser:
        url = f"http://127.0.0.1:{args.port}"

        def _open():
            time.sleep(1.2)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("ainovel.api:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
