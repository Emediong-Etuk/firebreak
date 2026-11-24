"""Append a record to evidence/submissions.jsonl (machine-written; never edit by hand).
Usage: ledger.py prepare N ZIP SUMMARY | ledger.py submitted N REF | ledger.py scored N SCORE"""
import datetime, hashlib, json, subprocess, sys
from pathlib import Path
L = Path(__file__).resolve().parents[1] / "evidence" / "submissions.jsonl"
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
kind, n = sys.argv[1], int(sys.argv[2])
rec = {"n": n, "event": kind, "utc": now}
if kind == "prepare":
    z = Path(sys.argv[3])
    rec.update(sha256=hashlib.sha256(z.read_bytes()).hexdigest(), zip=z.name,
               git_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               config_summary=sys.argv[4], status="prepared")
elif kind == "submitted":
    rec.update(ref=sys.argv[3], status="submitted")
elif kind == "scored":
    rec.update(public_score=float(sys.argv[3]), status="scored")
with L.open("a") as f:
    f.write(json.dumps(rec, sort_keys=True) + "\n")
print(json.dumps(rec))
