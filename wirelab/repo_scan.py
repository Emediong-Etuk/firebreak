"""Aggregate the per-snapshot scans (data/preimages/*/_scan.json) into evidence/wirelab/repo_scan.json:
wire-token occurrences and escape-token collisions across all 129 task snapshots."""
import glob, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
scans = [json.load(open(f)) for f in sorted(glob.glob(str(ROOT / "data/preimages/*/_scan.json")))]
tok = {}
for s in scans:
    for t, c in s["escape_like_tokens"].items():
        tok[t] = tok.get(t, 0) + c
out = {"snapshots": len(scans), "text_files": sum(s["files_scanned"] for s in scans),
       "bytes": sum(s["bytes_scanned"] for s in scans),
       "files_containing": {k: sum(len(s["hits"][k]) for s in scans) for k in scans[0]["hits"]},
       "escape_like_tokens_@@X@@": tok,
       "note": "binary files (NUL in first 4 KiB) skipped; .git objects are compressed and therefore skipped as binary"}
(ROOT / "evidence/wirelab/repo_scan.json").write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out))
