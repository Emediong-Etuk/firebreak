"""Replay one saved reproducer (evidence/wirelab/reproducers/*.json) on the pinned parser.
Usage: python -I wirelab/replay.py reproducers/<name>.json"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from parse_driver import Driver  # noqa: E402
p = Path(sys.argv[1])
if not p.is_absolute() and not p.exists():
    p = HERE.parent / "evidence" / "wirelab" / p
rec = json.loads(p.read_text())
if not rec.get("found"):
    sys.exit(f"{p.name}: no reproducer was found for this class ({rec.get('error')})")
d = Driver(timeout_s=2.0)
r = d.classify(rec["text"], rec["tool"], rec["args"])
d.close()
print(f"{p.name}: expected {rec['outcome']}, got {r['cls']}: {str(r.get('detail'))[:160]}")
sys.exit(0 if r["cls"] == rec["outcome"] else 1)
