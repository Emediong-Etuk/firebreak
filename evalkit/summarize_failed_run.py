"""Summarize a capture run that produced no task data -> evidence/runs/<name>/failure.json.

Reads a downloaded Kaggle kernel output dir (the notebook log + out/stage.json) and records how far the
run got, the error lines, and where the log stops, so the failure is evidence, not a recollection.

Usage: python -I evalkit/summarize_failed_run.py DOWNLOAD_DIR RUN_NAME [--note TEXT]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = re.compile(r"(Error|ERROR|Traceback|out of memory|Timeout waiting|SystemExit|did not terminate|"
                      r"non-default args|Custom allreduce|KV cache size|Maximum concurrency)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("download_dir"); ap.add_argument("run_name"); ap.add_argument("--note", default="")
    a = ap.parse_args()
    d = Path(a.download_dir)
    log_p = next(d.glob("*.log"))
    log = json.loads(log_p.read_text())
    stage_p = d / "out" / "stage.json"
    stages = json.loads(stage_p.read_text())["stages"] if stage_p.exists() else []
    lines, seen = [], set()
    for e in log:
        for ln in e.get("data", "").splitlines():
            if PATTERNS.search(ln):
                key = re.sub(r"pid=\d+|Worker_TP\d|\d\d:\d\d:\d\d", "", ln)[:200]
                if key not in seen:
                    seen.add(key)
                    lines.append({"t": round(e.get("time", 0)), "line": ln[:400]})
    out_files = sorted(str(p.relative_to(d)) for p in (d / "out").rglob("*") if p.is_file()) if (d / "out").exists() else []
    rep = {
        "run": a.run_name,
        "stages_reached": [s["stage"] for s in stages],
        "log_entries": len(log),
        "log_first_t": round(log[0].get("time", 0)) if log else None,
        "log_last_t": round(log[-1].get("time", 0)) if log else None,
        "log_last_line": (log[-1].get("data", "").splitlines() or [""])[-1][-300:] if log else None,
        "output_files": out_files,
        "key_lines": lines[:60],
        "note": a.note,
    }
    ev = ROOT / "evidence" / "runs" / a.run_name
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "failure.json").write_text(json.dumps(rep, indent=1) + "\n")
    print(json.dumps({k: v for k, v in rep.items() if k != "key_lines"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
