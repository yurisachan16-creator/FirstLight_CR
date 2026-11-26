import collections
import json
import sys

d = sys.argv[1]
rows = [json.loads(line) for line in open(f"{d}/matches.jsonl") if line.strip()]
print("matches", len(rows))
print("policy_matchup values", collections.Counter(str(r.get("policy_matchup")) for r in rows).most_common(8))
print("result sample", [r.get("result") for r in rows[:3]], "current_owners sample", [r.get("current_owners") for r in rows[:3]])
print("opponent ids", collections.Counter(str(r.get("opponent_checkpoint_id"))[:24] for r in rows).most_common(6))


def learner_outcome(row):
    """Return list of (+1 win, 0 draw, -1 loss) for each learner-controlled owner."""
    result = row.get("result")
    owners = row.get("current_owners") or []
    outcomes = []
    for owner in owners:
        value = None
        if isinstance(result, dict):
            value = result.get(str(owner), result.get(owner))
        elif isinstance(result, list) and len(result) > owner:
            value = result[owner]
        if isinstance(value, (int, float)):
            outcomes.append(1 if value > 0 else (-1 if value < 0 else 0))
    return outcomes


buckets = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    kind = str(r.get("policy_matchup"))
    phase = "early(1-27)" if r["update"] <= 27 else ("mid(28-55)" if r["update"] <= 55 else "late(56-83)")
    for outcome in learner_outcome(r):
        buckets[(kind, phase)][outcome] += 1
for key in sorted(buckets):
    c = buckets[key]
    n = sum(c.values())
    if n:
        print(f"{key[0]:<28} {key[1]:<12} n={n:4d}  win={c[1] / n:.0%}  draw={c[0] / n:.0%}  loss={c[-1] / n:.0%}")
rej = sum(1 for r in rows if r.get("rejected_actions"))
trunc = sum(1 for r in rows if r.get("truncated"))
print("matches with rejected actions:", rej, " truncated:", trunc)
