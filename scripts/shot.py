"""Save a PNG of one element of a page in a headless Chrome (Run 9, Step E).

    python scripts/shot.py --base https://... --path /roadmap --selector '#m3' --out autopilot/evidence/2026-10-31/live-1440 --name roadmap-m3.png --width 1440

Falls back to the whole first screen when the selector matches nothing. Reads pages only.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_measure import CHROME, Tab  # noqa: E402


async def run(a) -> dict:
    chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={a.port}", f"--user-data-dir={tempfile.mkdtemp()}",
                               f"--window-size={a.width},900", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                target = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json/list")) if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            tab = Tab(ws)
            await tab.call("Page.enable")
            await tab.call("Emulation.setDeviceMetricsOverride", width=a.width, height=900, deviceScaleFactor=1, mobile=a.width < 500)
            await tab.call("Page.navigate", url=a.base.rstrip("/") + a.path)
            for _ in range(40):
                await asyncio.sleep(0.5)
                if (await tab.call("Runtime.evaluate", expression="document.readyState", returnByValue=True))["result"].get("value") == "complete":
                    break
            await asyncio.sleep(2.5)
            expr = ("(() => { const e = document.querySelector(%s); if (!e) return null; const b = e.getBoundingClientRect(); "
                    "return {x: 0, y: b.top + scrollY, w: innerWidth, h: Math.min(b.height, 5000), sideways: document.documentElement.scrollWidth > innerWidth + 1}; })()" % json.dumps(a.selector))
            box = (await tab.call("Runtime.evaluate", expression=expr, returnByValue=True))["result"].get("value") or {"x": 0, "y": 0, "w": a.width, "h": 900, "sideways": None}
            shot = await tab.call("Page.captureScreenshot", format="png", captureBeyondViewport=True, clip={"x": 0, "y": box["y"], "width": box["w"], "height": box["h"], "scale": 1})
            Path(a.out).mkdir(parents=True, exist_ok=True)
            (Path(a.out) / a.name).write_bytes(base64.b64decode(shot["data"]))
            return box
    finally:
        chrome.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--path", required=True)
    ap.add_argument("--selector", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--port", type=int, default=9360)
    a = ap.parse_args()
    box = asyncio.run(run(a))
    print(f"{a.name}: {a.width}px, element height {round(box['h'])}, page scrolls sideways: {box.get('sideways')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
