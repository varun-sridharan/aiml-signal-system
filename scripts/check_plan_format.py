"""Validate plan/plan.md and plan/roadmap.md against plan/FORMAT.md.

    python scripts/check_plan_format.py                 # the two real files
    python scripts/check_plan_format.py PLAN ROADMAP    # any two files, for testing

Exits 0 when both pass, 1 with one line per violation otherwise:

    plan/plan.md:42: [plan.status-cell] Status must be exactly DONE, ... got 'Done'

The same check runs in three places, each one a later and more expensive place to catch
the same mistake: the git pre-commit hook, app startup (the app refuses to start), and
therefore the Railway healthcheck, which keeps the previous deployment serving.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.plan_format import check  # noqa: E402

DEFAULT = (ROOT / "plan" / "plan.md", ROOT / "plan" / "roadmap.md")


def main(argv: list[str]) -> int:
    if len(argv) not in (0, 2):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    plan, roadmap = (Path(a) for a in argv) if argv else DEFAULT
    for f in (plan, roadmap):
        if not f.is_file():
            print(f"{f}: [missing] file not found", file=sys.stderr)
            return 1
    errors = check(plan, roadmap)
    for e in errors:
        print(e)
    if errors:
        print(f"FAIL: {len(errors)} format error(s); see plan/FORMAT.md", file=sys.stderr)
        return 1
    print(f"OK: {plan.name} and {roadmap.name} match plan/FORMAT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
