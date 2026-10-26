"""Step D: the two gates side by side, from the saved runs. Usage: python stepD-summary.py ADJUDICATION_JSON > results.md"""
import collections, json, sys
from pathlib import Path
G = Path("autopilot/evidence/2026-10-26/stepD-gates")
adj = {a["id"]: a for a in json.load(open(sys.argv[1]))} if len(sys.argv) > 1 else {}

def load(path):
    return [json.loads(p.read_text()) for p in sorted(Path(path).glob("*-run*.json"))]

print("# Step D · the claim gate and the citation gate on the same inputs\n")
print("Five runs per cell. Fixed texts: the clean golden day (2026-08-04) and the four planted-fabrication cases. 'Caught' counts planted claims "
      "flagged across the runs; 'false alarms' are flags that the hand labels do not expect, per run.\n")
print("| case | gate | planted caught | false alarms per run | units flagged per run | $ per run | seconds per run |")
print("|---|---|---|---|---|---|---|")
rows = {"claims": [r for r in load(G / "fixed-v1-heading-bug") if r["gate"] == "claims"], "citations": load(G / "fixed-v2")}
for case in ("negative-01", "negative-02", "negative-03", "negative-04", "2026-08-04"):
    for gate in ("claims", "citations"):
        rs = sorted((r for r in rows[gate] if r["case"] == case), key=lambda r: r["run"])
        planted = sum(len(r["caught"]) for r in rs)
        caught = f"{sum(sum(r['caught'].values()) for r in rs)}/{planted}" if planted else "none planted"
        print(f"| {case} | {gate} | {caught} | {[r['false_alarms'] for r in rs]} | {[r['units_flagged'] for r in rs]} | "
              f"{sum(r['cost'] for r in rs) / len(rs):.4f} | {sum(r['seconds'] for r in rs) / len(rs):.0f} |")
tot = {g: sum(r["false_alarms"] for r in rows[g]) for g in rows}
print(f"\nFalse alarms over all 25 runs on fixed text: claims {tot['claims']}, citations {tot['citations']}.\n")

print("""**How to read this.** The fixed texts are already written, so the citation gate was fed by a stand-in citer (a model asked to restate each field word for word and cite it, leaving unsupported sentences uncited and marking pure judgement with `~`); in production the writer cites as it writes. That stand-in makes a planted sentence easy to catch by code (negative-03 was caught by the code rules in four of five runs) and makes the false-alarm count depend on how well it marks judgement. The first stand-in parser had a defect (a heading run into the next word; folder `fixed-v1-heading-bug`), so the citation arm was rerun in `fixed-v2`; the claim-gate rows come from the v1 folder because that arm was unaffected. The claim gate's units are claims and the citation gate's are sentences, so false-alarm counts are of different things; the switch rule fails either way (58 against 5). On W43 the two gates judged different writer outputs, so recall cannot be compared, and a gate that flags little looks good on false alarms; the adjudicator is one model, and 61 of the 126 citation flags it called judgement. Per-run `cost_usd` is the run's own calls; `ledger_delta_usd` in the W43 files also includes concurrent runs. The first trials (three editions and a two-case run) are in `stepD-trial/`.\n""")
print("## The 2026-W43 Reader output (stored first pass reused; the writer and the gate run fresh each time)\n")
print("| run | gate | publishes | $ | seconds | what stopped it |")
print("|---|---|---|---|---|---|")
w = G / "w43"
for gate in ("claims", "citations"):
    for n in range(1, 6):
        d = json.loads((w / f"{gate}-run{n}.json").read_text())
        if gate == "claims":
            why = "; ".join([f[:70] for f in d["faithfulness_failures"]] + [f[:80] for f in d["other_failures"]])
        else:
            why = f"{len(d['passing'])} of {d['story_count']} stories passed, {len(d['held'])} held, {len(d['blockers'])} blocking failures, {len(d['structural_failures'])} structural"
        print(f"| {n} | {gate} | {'yes' if d['publishes'] else 'no'} | {d['cost_usd']:.2f} | {d['seconds']:.0f} | {why} |")
if adj:
    print("\n### Adjudicated flags on the W43 text (a fresh reader checked every flag against the excerpts)\n")
    flags = json.load(open(sys.argv[2]))["flags"] if len(sys.argv) > 2 else []
    c = collections.Counter()
    for f in flags:
        c[(f["gate"], adj[f["id"]]["verdict"])] += 1
    print("| gate | flags | true alarms | false alarms (supported) | false alarms (judgement) | verbatim |")
    print("|---|---|---|---|---|---|")
    for g in ("claims", "citations"):
        n = sum(v for (gg, _), v in c.items() if gg == g)
        print(f"| {g} | {n} | {c[(g, 'TRUE_ALARM')]} | {c[(g, 'FALSE_ALARM_SUPPORTED')]} | {c[(g, 'FALSE_ALARM_JUDGEMENT')]} | {c[(g, 'VERBATIM')]} |")
