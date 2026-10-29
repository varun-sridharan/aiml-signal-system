"""Measure a page of this app in a headless Chrome from a test: serve the app on a free local port, load a path at a width, evaluate one expression.

Used for what only a layout engine can say (how many lines a headline takes at 1440px). `available()` is False without Chrome, and the test skips.
Reads pages only; nothing is sent anywhere but 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
sys.path.insert(0, str(REPO / "scripts"))


def available() -> bool:
    try:
        import websockets  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(CHROME)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def served(app):
    import uvicorn
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


async def _measure(base: str, path: str, width: int, expression: str):
    import websockets
    from site_measure import Tab
    port = _free_port()
    chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={port}", f"--user-data-dir={tempfile.mkdtemp()}",
                               f"--window-size={width},900", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                target = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list")) if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.25)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            tab = Tab(ws)
            await tab.call("Page.enable")
            await tab.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=width < 500)
            await tab.call("Page.navigate", url=base + path)
            for _ in range(60):
                await asyncio.sleep(0.25)
                if (await tab.call("Runtime.evaluate", expression="document.readyState", returnByValue=True))["result"].get("value") == "complete":
                    break
            await asyncio.sleep(1.5)                                    # fonts
            return (await tab.call("Runtime.evaluate", expression=expression, returnByValue=True))["result"].get("value")
    finally:
        chrome.terminate()


def measure(base: str, path: str, width: int, expression: str):
    return asyncio.run(_measure(base, path, width, expression))
