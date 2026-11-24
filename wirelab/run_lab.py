"""Wire Lab runner: build corpora, classify every case on the pinned parser, write evidence.

  python -I wirelab/run_lab.py --quick      # C1 5k + C2 + C3   (≈ minutes)
  python -I wirelab/run_lab.py --full       # C1 100k + C2 + C3

Outputs (machine-written):
  evidence/wirelab/results/<corpus>.jsonl.gz  one line per case (id, cls, detail, meta)
  evidence/wirelab/summary.json             counts per corpus x class, top fault signatures
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
EV = ROOT / "evidence" / "wirelab"
WORK = ROOT / "wirelab" / "corpora"


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open()] if p.exists() else []


def classify_shard(cases_path: Path, shard: int, nshards: int, out_path: Path, timeout: float) -> None:
    from parse_driver import Driver
    from render import render_tool_call
    d = Driver(timeout_s=timeout)
    with out_path.open("w") as f:
        for i, rec in enumerate(load_jsonl(cases_path)):
            if i % nshards != shard:
                continue
            text = rec.get("text") or render_tool_call(rec["tool"], rec["args"])
            r = d.classify(text, rec["tool"], rec["args"])
            meta = {k: rec.get(k) for k in ("corpus", "tool", "mode", "op", "task", "kind", "has_array", "src")}
            row = {"id": rec["id"], "cls": r["cls"], "detail": str(r.get("detail", ""))[:240], **meta}
            if r["cls"] not in ("OK",):
                row["text"] = text[:2000]
                if "parsed_args" in r:
                    row["parsed_keys"] = sorted(r["parsed_args"])[:12]
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    d.close()


def run_parallel(cases_path: Path, out_path: Path, timeout: float, nproc: int) -> list[dict]:
    parts = [out_path.with_suffix(f".part{i}") for i in range(nproc)]
    procs = [subprocess.Popen([sys.executable, "-I", __file__, "--_shard", str(cases_path), str(i), str(nproc),
                               str(parts[i]), str(timeout)]) for i in range(nproc)]
    for p in procs:
        if p.wait() != 0:
            raise SystemExit(f"shard failed: {p.args}")
    rows = []
    for p in parts:
        rows += load_jsonl(p)
        p.unlink()
    rows.sort(key=lambda r: r["id"])
    import gzip
    # deterministic gzip (mtime=0) so evidence diffs are content-only
    with gzip.GzipFile(out_path.with_suffix(".jsonl.gz"), "wb", mtime=0) as gz:
        for r in rows:
            gz.write((json.dumps(r, ensure_ascii=False) + "\n").encode())
    return rows


def signature(row: dict) -> str:
    d = row["detail"]
    if row["cls"] == "MANGLED" and row.get("parsed_keys"):
        weird = [k for k in row["parsed_keys"] if not k.replace("_", "").isalnum()]
        if weird:
            return f"MANGLED: junk key {weird[0][:30]!r}"
    if row["cls"] == "REJECTED":
        return "REJECTED: " + d.split(":")[0][:60] + (": " + d.split(":")[-1].strip()[:40] if ":" in d else "")
    return f"{row['cls']}: {d[:60]}"


def summarize(name: str, rows: list[dict], by: str | None) -> dict:
    cnt = collections.Counter(r["cls"] for r in rows)
    out = {"n": len(rows), "classes": {c: cnt.get(c, 0) for c in ("OK", "MANGLED", "DROPPED", "REJECTED", "HANG", "CRASH")}}
    out["fault_rate"] = round(1 - cnt.get("OK", 0) / len(rows), 4) if rows else None
    if by:
        grp = collections.defaultdict(collections.Counter)
        for r in rows:
            grp[str(r.get(by))][r["cls"]] += 1
        out[f"by_{by}"] = {g: dict(c) for g, c in sorted(grp.items())}
    sigs = collections.Counter(signature(r) for r in rows if r["cls"] != "OK")
    out["top_signatures"] = sigs.most_common(8)
    return out


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--_shard":
        classify_shard(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5]), float(sys.argv[6]))
        return 0
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true")
    g.add_argument("--full", action="store_true")
    ap.add_argument("--nproc", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--timeout", type=float, default=0.5, help="watchdog seconds per case")
    a = ap.parse_args()
    n_c1 = 100_000 if a.full else 5_000

    import corpora
    (EV / "results").mkdir(parents=True, exist_ok=True)
    for sub in ("c1_fuzz", "c3_deviations"):
        (WORK / sub).mkdir(parents=True, exist_ok=True)

    c2 = load_jsonl(WORK / "c2_gold" / "calls.jsonl")
    for rec in c2:
        rec["corpus"] = "c2"
    built = {"c1": corpora.c1(n_c1), "c2": c2, "c3": corpora.c3(c2)}
    paths = {"c1": WORK / "c1_fuzz" / "cases.jsonl", "c2": WORK / "c2_gold" / "calls.jsonl",
             "c3": WORK / "c3_deviations" / "cases.jsonl"}
    for k in ("c1", "c3"):
        with paths[k].open("w") as f:
            for r in built[k]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "pinned_parser_sha256": json.loads((HERE / "pinned" / "hashes.json").read_text())["gemma4_tool_parser.py"],
               "watchdog_s": a.timeout, "run": "full" if a.full else "quick", "corpora": {}}
    by = {"c1": "mode", "c2": "kind", "c3": "op"}
    for k in ("c1", "c2", "c3"):
        t0 = time.time()
        rows = run_parallel(paths[k], EV / "results" / f"{k}.jsonl", a.timeout, a.nproc)
        s = summarize(k, rows, by[k])
        if k == "c1":
            s["arrays_vs_scalars"] = {
                "with_array_arg": summarize(k, [r for r in rows if r.get("has_array")], None)["classes"],
                "scalar_only": summarize(k, [r for r in rows if not r.get("has_array")], None)["classes"]}
            s["faithful_scalar_only"] = summarize(k, [r for r in rows if r.get("mode") == "faithful"
                                                      and not r.get("has_array")], None)["classes"]
        if k in ("c2", "c3"):
            tasks_bad = sorted({r["task"] for r in rows if r["cls"] != "OK" and r.get("task")})
            s["tasks_with_any_fault"] = len(tasks_bad)
        s["seconds"] = round(time.time() - t0, 1)
        summary["corpora"][k] = s
        print(k, json.dumps(s["classes"]), f"{s['seconds']}s", flush=True)
    (EV / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
