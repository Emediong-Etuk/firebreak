"""Stream each task snapshot: extract only files touched by patch/test_patch into
data/preimages/<instance_id>/, scan ALL text files for wire-relevant byte patterns, delete tgz.
Writes data/preimages/<id>/_scan.json. Resumable (skips ids with _scan.json).
Usage: python -I tools/stream_snapshots.py [--limit N]"""
import argparse, json, re, subprocess, sys, tarfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PATTERNS = {
    "marker_<|\"|>": re.compile(rb'<\|"\|>'),
    "tool_call_end": re.compile(rb"<tool_call\|>"),
    "tool_call_start": re.compile(rb"<\|tool_call>"),
}
ESC = re.compile(rb"@@[A-Z0-9_]{1,12}@@")


def touched(patch: str) -> set[str]:
    out = set()
    for line in patch.splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            out.add(line[6:].split("\t")[0])
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    tasks = [json.loads(l) for l in open(DATA / "tasks.jsonl")]
    done = 0
    for t in tasks:
        iid = t["instance_id"]
        outdir = DATA / "preimages" / iid
        if (outdir / "_scan.json").exists():
            continue
        tmp = DATA / "_tgz"; tmp.mkdir(exist_ok=True)
        t0 = time.time()
        for attempt in range(4):
            r = subprocess.run([sys.executable, str(ROOT / "tools/kaggle_cli.py"), "competitions", "download",
                                "gemma-4-developer-agent", "-f", f"snapshots/{iid}.tgz", "-p", str(tmp), "-q"],
                               capture_output=True, text=True)
            tgz = next(tmp.rglob(f"{iid}.tgz"), None)
            if tgz:
                break
            time.sleep(5 * (attempt + 1))
        if not tgz:
            print("FAIL download", iid, r.stderr[-300:], flush=True); continue
        want = touched(t["patch"]) | touched(t["test_patch"])
        scan = {"instance_id": iid, "files_scanned": 0, "bytes_scanned": 0,
                "hits": {k: [] for k in PATTERNS}, "escape_like_tokens": {}, "extracted": [], "members": 0}
        outdir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tgz, "r:*") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                scan["members"] += 1
                name = m.name.split("/", 1)[1] if "/" in m.name else m.name  # strip top dir
                f = tf.extractfile(m)
                if f is None:
                    continue
                b = f.read()
                if b"\0" in b[:4096]:
                    continue  # binary
                scan["files_scanned"] += 1; scan["bytes_scanned"] += len(b)
                for k, rx in PATTERNS.items():
                    if rx.search(b):
                        scan["hits"][k].append(name)
                for tok in set(ESC.findall(b)):
                    scan["escape_like_tokens"][tok.decode()] = scan["escape_like_tokens"].get(tok.decode(), 0) + 1
                for cand in (name, m.name):
                    if cand in want:
                        dest = outdir / "files" / cand
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(b)
                        scan["extracted"].append(cand)
                        break
                if scan["members"] == 1:
                    scan["top_dir_example"] = m.name
        scan["want"] = sorted(want)
        scan["missing"] = sorted(w for w in want if w not in scan["extracted"])
        scan["seconds"] = round(time.time() - t0, 1)
        (outdir / "_scan.json").write_text(json.dumps(scan, indent=1))
        tgz.unlink()
        done += 1
        print(f"{iid}: {scan['files_scanned']} files, extracted {len(scan['extracted'])}/{len(want)}, "
              f"markers={len(scan['hits']['marker_<|\"|>'])}, {scan['seconds']}s", flush=True)
        if a.limit and done >= a.limit:
            break


if __name__ == "__main__":
    main()
