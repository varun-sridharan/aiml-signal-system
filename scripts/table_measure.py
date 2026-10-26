"""Measure the tables of the three document pages and the Edition thread headline in a headless Chrome (Run 7, Steps B and F).

    python scripts/table_measure.py --base http://localhost:8765 --out autopilot/evidence/2026-10-29/stepB-after-1440 --width 1440

For each of /design/System-Design.html, /roadmap and /plan it lists every table cell's words that wrapped inside the word (a break is
legal only after whitespace, a hyphen, / _ . or an opening bracket), the tables' columns and widest cell, and whether the page scrolls
sideways. For /weekly-digest it records the thread headline's size, width and line count. It saves a PNG of each table and of the
thread. Nothing is sent anywhere: pages are read from --base.
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

DOC_PAGES = [("system_design", "/design/System-Design.html"), ("roadmap", "/roadmap"), ("plan", "/plan")]

MEASURE = r"""
(() => {
  const LEGAL = new Set([' ', '\n', '\t', '-', '/', '_', '.', '(', '‑']);
  const splits = [];
  const tables = [...document.querySelectorAll('.layout-doc table')];
  const info = tables.map((t, i) => {
    const cells = [...t.querySelectorAll('td,th')];
    const words = (c) => (c.textContent || '').trim().split(/\s+/).filter(Boolean).length;
    for (const c of cells) {
      const walker = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const text = node.nodeValue;
        const re = /\S+/g; let m;
        while ((m = re.exec(text))) {
          const range = document.createRange();
          range.setStart(node, m.index); range.setEnd(node, m.index + m[0].length);
          const rects = [...range.getClientRects()];
          if (rects.length < 2 || Math.max(...rects.map(r => r.top)) - Math.min(...rects.map(r => r.top)) < 4) continue;
          let prevTop = null;
          for (let k = 0; k < m[0].length; k++) {
            const r2 = document.createRange(); r2.setStart(node, m.index + k); r2.setEnd(node, m.index + k + 1);
            const b = r2.getBoundingClientRect();
            if (!b.height) continue;
            if (prevTop !== null && Math.abs(b.top - prevTop) > 4 && k > 0 && !LEGAL.has(m[0][k - 1])) {
              splits.push({table: i, word: m[0], before: m[0].slice(0, k), after: m[0].slice(k)});
              break;
            }
            prevTop = b.top;
          }
        }
      }
    }
    const first = t.querySelectorAll('tbody tr > td:first-child');
    const b = t.getBoundingClientRect(), wrap = t.closest('.table-wrap');
    const w0 = first[0] ? getComputedStyle(first[0]) : null;
    return {index: i, columns: t.querySelectorAll('thead th').length, rows: t.querySelectorAll('tbody tr').length,
      widest_cell_words: Math.max(0, ...cells.map(words)), table_w: Math.round(b.width), wrap_w: wrap ? Math.round(wrap.clientWidth) : null,
      scrolls: wrap ? wrap.scrollWidth > wrap.clientWidth + 1 : null,
      first_col_label_class: first.length ? [...first].every(c => c.classList.contains('label')) : null,
      first_col_bold: [...first].filter(c => c.querySelector('b,strong') || parseInt(getComputedStyle(c).fontWeight) >= 600).length,
      first_col_style: w0 && {family: w0.fontFamily.split(',')[0].replace(/"/g, ''), size: w0.fontSize, weight: w0.fontWeight, transform: w0.textTransform, white_space: w0.whiteSpace}};
  });
  const big = document.querySelector('.thread-lines, .s-thread .big');
  const bigr = big ? big.getBoundingClientRect() : null;
  const lineEls = big ? [...big.querySelectorAll('.line')] : [];
  const lh = big ? parseFloat(getComputedStyle(big).lineHeight) : 0;
  return {url: location.pathname, viewport: innerWidth, page_scrolls_sideways: document.documentElement.scrollWidth > innerWidth + 1,
    tables: info, mid_word_splits: splits,
    thread: big && {font_size: getComputedStyle(big).fontSize, max_width: getComputedStyle(big).maxWidth, text_wrap: getComputedStyle(big).textWrap,
      width: Math.round(bigr.width), height: Math.round(bigr.height), rendered_lines: lh ? Math.round(bigr.height / lh) : null,
      lines: lineEls.map(l => l.textContent.trim())}};
})()
"""

CLIPS = r"""
(() => [...document.querySelectorAll('.layout-doc .table-wrap, .layout-doc .plan-row-notes, .thread-lines')].map((e, i) => {
  const r = e.getBoundingClientRect(); return {i, x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height};
}))()
"""


async def load(tab, url):
    await tab.call("Page.navigate", url=url)
    for _ in range(40):
        await asyncio.sleep(0.5)
        state = await tab.call("Runtime.evaluate", expression="document.readyState", returnByValue=True)
        if state["result"].get("value") == "complete":
            break
    await asyncio.sleep(2.5)


async def run(base: str, out: Path, width: int, port: int, pages: list) -> dict:
    profile = tempfile.mkdtemp(prefix="table-measure-")
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
            await tab.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=width < 500)
            for name, path in pages:
                await load(tab, base + path)
                got = await tab.call("Runtime.evaluate", expression=MEASURE, returnByValue=True)
                results[name] = got["result"].get("value")
                clips = (await tab.call("Runtime.evaluate", expression=CLIPS, returnByValue=True))["result"].get("value") or []
                for c in clips[:30]:
                    if c["w"] < 2 or c["h"] < 2:
                        continue
                    shot = await tab.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                                          clip={"x": max(0, c["x"] - 8), "y": max(0, c["y"] - 8), "width": min(c["w"] + 16, width), "height": min(c["h"] + 16, 6000), "scale": 1})
                    (out / f"{name}-{c['i']:02d}.png").write_bytes(base64.b64decode(shot["data"]))
        return results
    finally:
        chrome.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8765")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--port", type=int, default=9334)
    ap.add_argument("--digest-path", default="/weekly-digest")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = DOC_PAGES + [("weekly_digest", args.digest_path)]
    results = asyncio.run(run(args.base.rstrip("/"), out, args.width, args.port, pages))
    (out / "measurements.json").write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    for name, m in results.items():
        if not m:
            print(f"{name}: no measurement")
            continue
        print(f"{name:<14} tables {len(m['tables'])}, mid-word splits {len(m['mid_word_splits'])}, page scrolls sideways {m['page_scrolls_sideways']}"
              + (f", thread {m['thread']['font_size']} in {m['thread']['width']}px, {m['thread']['rendered_lines']} lines" if m.get("thread") else ""))
        for s in m["mid_word_splits"][:8]:
            print("    split:", s["before"], "|", s["after"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
