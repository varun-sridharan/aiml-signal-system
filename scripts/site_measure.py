"""Measure the header and body of the five site pages in a headless Chrome, and save a screenshot of each page's top.

    python scripts/site_measure.py --base http://localhost:8765 --out autopilot/evidence/2026-10-27/stepC-after --width 1440

Opens Google Chrome headless with the DevTools protocol, visits the five pages, and for each records the wordmark (font size,
family, weight, position), the header (position, height, background, blur), a nav item, the body and the first h1; it writes
measurements.json and one PNG per page. Nothing is sent anywhere: the pages are read from --base. Used for the before and after
of Run 5, Step C (bk-tokens-css), where all five headers must match exactly.
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

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PAGES = [("product", "/application/Product.html"), ("weekly_digest", "/weekly-digest"), ("system_design", "/design/System-Design.html"),
         ("roadmap", "/roadmap"), ("plan", "/plan")]

MEASURE = r"""
(() => {
  const cs = (e) => e ? getComputedStyle(e) : null;
  const r = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)}; };
  const all = [...document.querySelectorAll('a,span,div,header')];
  const wm = all.find(e => e.children.length <= 1 && /^AI\/ML Signal\.?$/.test((e.textContent || '').trim()));
  let head = wm; while (head && !['fixed', 'sticky'].includes(getComputedStyle(head).position)) head = head.parentElement;
  const nav = head ? [...head.querySelectorAll('a,span')].find(e => (e.textContent || '').trim() === 'System Design') : null;
  const h1 = document.querySelector('h1');
  return {url: location.pathname, viewport: innerWidth,
    wordmark: wm && {size: cs(wm).fontSize, family: cs(wm).fontFamily.split(',')[0].replace(/"/g, ''), weight: cs(wm).fontWeight, rect: r(wm)},
    header: head && {position: cs(head).position, background: cs(head).backgroundColor, blur: cs(head).backdropFilter, rect: r(head), tag: head.tagName},
    nav: nav && {size: cs(nav).fontSize, family: cs(nav).fontFamily.split(',')[0].replace(/"/g, '')},
    body: {size: cs(document.body).fontSize, family: cs(document.body).fontFamily.split(',')[0].replace(/"/g, '')},
    h1: h1 && {size: cs(h1).fontSize, family: cs(h1).fontFamily.split(',')[0].replace(/"/g, ''), weight: cs(h1).fontWeight}};
})()
"""


class Tab:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]


async def run(base: str, out: Path, width: int, port: int) -> dict:
    profile = tempfile.mkdtemp(prefix="site-measure-")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                               f"--window-size={width},900", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
                target = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("Chrome did not start")
        results = {}
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            tab = Tab(ws)
            await tab.call("Page.enable")
            await tab.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=False)
            for name, path in PAGES:
                await tab.call("Page.navigate", url=base + path)
                for _ in range(40):
                    await asyncio.sleep(0.5)
                    state = await tab.call("Runtime.evaluate", expression="document.readyState", returnByValue=True)
                    if state["result"].get("value") == "complete":
                        break
                await asyncio.sleep(3.0)                      # fonts, and the old snapshots' own unpacking
                got = await tab.call("Runtime.evaluate", expression=MEASURE, returnByValue=True)
                results[name] = got["result"].get("value")
                shot = await tab.call("Page.captureScreenshot", format="png", clip={"x": 0, "y": 0, "width": width, "height": 520, "scale": 1})
                (out / f"{name}.png").write_bytes(base64.b64decode(shot["data"]))
        return results
    finally:
        chrome.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8765")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--port", type=int, default=9333)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(run(args.base.rstrip("/"), out, args.width, args.port))
    (out / "measurements.json").write_text(json.dumps(results, indent=1) + "\n")
    for name, m in results.items():
        if not m:
            print(f"{name}: no measurement")
            continue
        w, h, n, b, t = m.get("wordmark") or {}, m.get("header") or {}, m.get("nav") or {}, m.get("body") or {}, m.get("h1") or {}
        print(f"{name:<14} wordmark {w.get('size')} {w.get('family')} at x={(w.get('rect') or {}).get('x')}; header {h.get('position')} h={(h.get('rect') or {}).get('h')}; "
              f"nav {n.get('size')} {n.get('family')}; body {b.get('size')} {b.get('family')}; h1 {t.get('size')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
