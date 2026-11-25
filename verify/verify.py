"""Firebreak verification kit.

  python verify/verify.py lite   CPU only, no Kaggle account (~5 min):
      1. download vllm==0.19.1 from PyPI, extract the gemma4 parser, check its sha256 against
         wirelab/pinned/hashes.json (the parser file is byte-identical to the competition wheelhouse's)
      2. vendored parser is regenerated verbatim from that file
      3. F1 reproduced (array hang), F2 signature reproduced, all shrunk reproducers replay
      4. Wire Lab fast tests, safe-edit + airlock unit/property tests, guarded-parser test
      5. committed submission zip(s) are byte-reproducible from the repo
  python verify/verify.py full   lite + data-dependent steps (needs competition data in data/):
      C2 gold replay, C1 100k/C3 runs, expressibility, official-compiler audit

Prints a table, writes evidence/verify_report.json, exits non-zero on any failure.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
HASHES = json.loads((ROOT / "wirelab/pinned/hashes.json").read_text())
PYPI_JSON = "https://pypi.org/pypi/vllm/0.19.1/json"
PARSER = "vllm/tool_parsers/gemma4_tool_parser.py"


def step(name, fn, results):
    t0 = time.time()
    try:
        detail = fn() or ""
        ok = True
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {str(e)[-400:]}"
    results.append({"step": name, "ok": ok, "seconds": round(time.time() - t0, 1), "detail": str(detail)[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name:46} {results[-1]['seconds']:>6}s  {str(detail)[:90]}", flush=True)


def run(*cmd, cwd=ROOT):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stdout + r.stderr)[-600:])
    return r.stdout


def pypi_parser():
    meta = json.load(urllib.request.urlopen(PYPI_JSON, timeout=60))
    whl = next(u for u in meta["urls"] if u["filename"].endswith("manylinux_2_31_x86_64.whl"))
    cache = ROOT / ".cache"; cache.mkdir(exist_ok=True)
    dest = cache / whl["filename"]
    if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != whl["digests"]["sha256"]:
        urllib.request.urlretrieve(whl["url"], dest)
    got_whl = hashlib.sha256(dest.read_bytes()).hexdigest()
    assert got_whl == HASHES["vllm_wheel_pypi"], f"PyPI wheel sha256 {got_whl} != pinned"
    with zipfile.ZipFile(dest) as z:
        src = z.read(PARSER)
    sha = hashlib.sha256(src).hexdigest()
    assert sha == HASHES["gemma4_tool_parser.py"], f"parser sha256 {sha} != pinned"
    assert src == (ROOT / "wirelab/pinned/gemma4_tool_parser.py.orig").read_bytes()
    return f"parser {sha[:12]} == pinned (wheel {got_whl[:12]})"


def zips_reproducible():
    out = []
    for z, src in (("dist/firebreak-lite.zip", "variants/firebreak-lite"), ("dist/firebreak-v2.zip", "variants/firebreak-v2"),
                   ("dist/firebreak-v2-5m.zip", "variants/firebreak-v2-5m")):
        with tempfile.TemporaryDirectory() as td:
            run(PY, "-I", "verify/build_zip.py", src, f"{td}/x.zip")
            a = hashlib.sha256((ROOT / z).read_bytes()).hexdigest()
            b = hashlib.sha256(Path(td, "x.zip").read_bytes()).hexdigest()
            assert a == b, f"{z}: committed {a[:12]} != rebuilt {b[:12]}"
            out.append(f"{Path(z).name} {a[:12]}")
    return ", ".join(out)


def f1():
    run(PY, "-I", "wirelab/repro_f1.py", "--seconds", "2")
    rep = json.loads((ROOT / "evidence/wirelab/F1.json").read_text())
    assert rep["reproduced"], "F1 not reproduced"
    hang = [p["id"] for p in rep["probes"] if p["outcome"] == "HANG"]
    return f"HANG on {len(hang)} probes, control OK"


def pytest(*paths):
    o = run(PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths)
    return o.strip().splitlines()[-1]


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "lite"
    res: list[dict] = []
    step("pinned parser from PyPI wheel (sha256)", pypi_parser, res)
    step("F1 reproduced on pinned parser", f1, res)
    step("Wire Lab tests (hashes, vendored, F2, reproducers)", lambda: pytest("wirelab/tests"), res)
    step("safe-edit + airlock tests", lambda: pytest("verify/tests"), res)
    step("guarded parser == pinned (local eval shim)", lambda: pytest("evalkit/tests"), res)
    step("submission zips byte-reproducible", zips_reproducible, res)
    if mode == "full":
        step("C2 gold replay (129 tasks)", lambda: run(PY, "-I", "wirelab/gold.py").strip().splitlines()[2], res)
        step("Wire Lab full (C1 100k, C2, C3)", lambda: run(PY, "-I", "wirelab/run_lab.py", "--full").strip(), res)
        step("safe-edit expressibility + C5", lambda: run(PY, "-I", "wirelab/expressibility.py").strip()[:200], res)
        for z in ("dist/firebreak-lite.zip", "dist/firebreak-v2.zip", "dist/firebreak-v2-5m.zip"):
            step(f"official compiler audit ({Path(z).stem})",
                 lambda z=z: run(PY, "-I", "verify/check_submission.py", z).strip().splitlines()[-1], res)
    rep = {"mode": mode, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": res,
           "ok": all(r["ok"] for r in res)}
    (ROOT / "evidence/verify_report.json").write_text(json.dumps(rep, indent=1) + "\n")
    print("ALL PASS" if rep["ok"] else "FAILURES: " + ", ".join(r["step"] for r in res if not r["ok"]))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
