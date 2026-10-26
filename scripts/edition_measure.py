"""Measure the Edition's four section headlines and its type scale in a headless Chrome (Run 8, Steps A, B, F and G).

    python scripts/edition_measure.py --base http://localhost:8765 --out autopilot/evidence/2026-10-30/after-1440 --width 1440

Records, for the thread, the Learning headline, the Stories headline, a story title, the featured story title and the closing lines: text,
words, characters, figures, font size, width and rendered lines; plus the body size and whether the page scrolls sideways. Saves a PNG of
the top of the page (all headlines) and, with --product, of the Product hero and the "Where this is going" section. Reads pages only.
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

MEASURE = r"""
(() => {
  const info = (el, key) => {
    if (!el) return {key, found: false};
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2;
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    return {key, found: true, text, words: text.split(' ').filter(Boolean).length, chars: text.length,
      figures: (text.match(/\d[\d,]*(?:\.\d+)?/g) || []).length, font_size: cs.fontSize, line_height: cs.lineHeight,
      max_width: cs.maxWidth, text_wrap: cs.textWrap, width: Math.round(r.width), height: Math.round(r.height), lines: Math.round(r.height / lh)};
  };
  const heads = [...document.querySelectorAll('.sec-headline, .big36, .big44')];
  const sections = ['thread', 'learning', 'stories', 'leaders'];
  const out = {url: location.pathname, viewport: innerWidth, page_scrolls_sideways: document.documentElement.scrollWidth > innerWidth + 1,
    body_font_size: getComputedStyle(document.body).fontSize,
    headlines: heads.map((h, i) => info(h, sections[i] || 'headline-' + i)),
    story_title: info(document.querySelector('.story:not(.feature) h3'), 'story'),
    feature_title: info(document.querySelector('.story.feature h3'), 'feature'),
    closing: info(document.querySelector('.closing .cl'), 'closing'),
    thread_body: info(document.querySelector('.thread-body'), 'thread-body')};
  const top = heads.length ? heads[heads.length - 1].getBoundingClientRect() : null;
  out.top_clip = top ? {y: Math.max(0, document.querySelector('.s-thread, .big').getBoundingClientRect().top + scrollY - 80), h: top.bottom + scrollY + 60} : null;
  return out;
})()
"""

PRODUCT = r"""
(() => {
  const hero = document.querySelector('.hero, .layout-landing .band');
  const where = document.getElementById('where-this-is-going');
  const box = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return {y: b.top + scrollY, h: b.height}; };
  const sec = where;
  return {hero: box(hero), where: box(sec), page_scrolls_sideways: document.documentElement.scrollWidth > innerWidth + 1};
})()
"""


async def load(tab, url):
    await tab.call("Page.navigate", url=url)
    for _ in range(40):
        await asyncio.sleep(0.5)
        st = await tab.call("Runtime.evaluate", expression="document.readyState", returnByValue=True)
        if st["result"].get("value") == "complete":
            break
    await asyncio.sleep(2.5)


async def shot(tab, out, name, y, h, width):
    s = await tab.call("Page.captureScreenshot", format="png", captureBeyondViewport=True, clip={"x": 0, "y": y, "width": width, "height": min(h, 6000), "scale": 1})
    (out / name).write_bytes(base64.b64decode(s["data"]))


async def run(base, out, width, port, digest, product):
    chrome = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={port}", f"--user-data-dir={tempfile.mkdtemp()}",
                               f"--window-size={width},900", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                target = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list")) if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        results = {}
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            tab = Tab(ws)
            await tab.call("Page.enable")
            await tab.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=width < 500)
            await load(tab, base + digest)
            m = (await tab.call("Runtime.evaluate", expression=MEASURE, returnByValue=True))["result"]["value"]
            results["edition"] = m
            if m.get("top_clip"):
                await shot(tab, out, "edition-top.png", m["top_clip"]["y"], m["top_clip"]["h"] - m["top_clip"]["y"], width)
            if product:
                await load(tab, base + "/application/Product.html")
                p = (await tab.call("Runtime.evaluate", expression=PRODUCT, returnByValue=True))["result"]["value"]
                results["product"] = p
                if p.get("hero"):
                    await shot(tab, out, "product-hero.png", p["hero"]["y"], min(p["hero"]["h"], 1600), width)
                if p.get("where"):
                    await shot(tab, out, "product-where-this-is-going.png", p["where"]["y"], p["where"]["h"], width)
        return results
    finally:
        chrome.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8765")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--port", type=int, default=9350)
    ap.add_argument("--digest-path", default="/weekly-digest")
    ap.add_argument("--product", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = asyncio.run(run(a.base.rstrip("/"), out, a.width, a.port, a.digest_path, a.product))
    (out / "measurements.json").write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    e = res["edition"]
    print(f"width {a.width}: body {e['body_font_size']}, sideways scroll {e['page_scrolls_sideways']}")
    for h in e["headlines"] + [e["story_title"], e["feature_title"], e["closing"]]:
        if h.get("found"):
            print(f"  {h['key']:<10} {h['font_size']:>5} {h['words']:>3} words {h['chars']:>4} chars {h['figures']} figures {h['lines']} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
