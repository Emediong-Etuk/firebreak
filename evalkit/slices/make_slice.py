"""Seeded 40-task slice stratified by repo and by triple-quote exposure of the minimal gold fix
(from wirelab C2). Writes evalkit/slices/slice40.json."""
import json, random
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
tasks = [json.loads(l) for l in open(ROOT / "data/tasks.jsonl")]
calls = [json.loads(l) for l in open(ROOT / "wirelab/corpora/c2_gold/calls.jsonl")]
tq = {c["task"] for c in calls if c["kind"] in ("hunk", "new_file")
      and any(isinstance(v, str) and ('"""' in v or "'''" in v) for v in c["args"].values())}
QUOTA = {"fastapi/fastapi": 20, "Textualize/rich": 15, "psf/requests": 4, "encode/httpx": 1}
rng = random.Random(20261124)
pick = []
for repo, q in QUOTA.items():
    ids = sorted(t["instance_id"] for t in tasks if t["repo"] == repo)
    a = [i for i in ids if i in tq]; b = [i for i in ids if i not in tq]
    rng.shuffle(a); rng.shuffle(b)
    na = min(len(a), q // 2); pick += a[:na] + b[:q - na]
out = {"seed": 20261124, "n": len(pick), "quota": QUOTA, "triple_quote_exposed": sorted(set(pick) & tq),
       "task_ids": sorted(pick)}
(ROOT / "evalkit/slices/slice40.json").write_text(json.dumps(out, indent=1) + "\n")
print(len(pick), "tasks,", len(out["triple_quote_exposed"]), "TQ-exposed")
