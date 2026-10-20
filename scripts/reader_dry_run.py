"""A free dry run of the Reader's discovery: what each feed and index yields, with no model call.

    python scripts/reader_dry_run.py [--out FILE]

Fetches every feed and index with plain HTTP (the same fetch the Reader's discovery uses), then runs the
window and relevance filters inside a transaction that is rolled back, so nothing is stored. It prints,
per feed: candidates, dated inside the window, undated, and relevant to the interest graph. It spends
nothing and writes nothing to the database.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import budget, graph, reader, sources  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    stats = reader.Stats()
    candidates = reader.discover(stats)
    lines = [f"Reader dry run at {now:%Y-%m-%d %H:%M} UTC · window {reader.WINDOW.days} days · "
             f"{stats.feeds_ok} of {len(reader.FEEDS)} feeds and indexes ok · no model call, nothing stored", ""]
    with budget.connect(autocommit=False) as conn:
        conn.execute("SELECT 1")
        reader.prepare(conn)
        by_feed: dict[str, Counter] = {f.id: Counter() for f in reader.FEEDS}
        for c in candidates:
            row = by_feed[c.feed.id]
            row["candidates"] += 1
            if c.published is None:
                row["undated"] += 1
            elif now - c.published <= reader.WINDOW:
                row["in window"] += 1
            else:
                continue
            score, _terms = graph.score(conn, reader.USER_ID, f"{c.title}. {c.summary}")
            row["relevant"] += score > 0
            row["blocked"] += sources.is_blocked(sources.domain_of(c.url))
        conn.rollback()
    cols = ("candidates", "in window", "undated", "relevant", "blocked")
    lines.append(f"{'feed':<20}" + "".join(f"{c:>12}" for c in cols))
    for feed in reader.FEEDS:
        row = by_feed[feed.id]
        lines.append(f"{feed.id:<20}" + "".join(f"{row[c]:>12}" for c in cols))
    total = Counter()
    for row in by_feed.values():
        total.update(row)
    lines.append(f"{'TOTAL':<20}" + "".join(f"{total[c]:>12}" for c in cols))
    if stats.feed_errors:
        lines += ["", "feed errors: " + ", ".join(stats.feed_errors)]
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
