"""Build (1) the private dataset dir with the eval kit and (2) the GPU capture notebook.
Usage: build_capture.py OWNER [--model gemma-4-e4b-it-qat-w4a16-ct] [--accel t4x2|l4x4] [--limit N] [--deadline-minutes M]"""
import argparse, json, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser(); ap.add_argument("owner")
ap.add_argument("--model", default="gemma-4-e4b-it-qat-w4a16-ct")
ap.add_argument("--accel", default="t4x2", choices=["t4x2", "l4x4"])
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--slug", default="firebreak-capture")
ap.add_argument("--workers", type=int, default=3)
ap.add_argument("--arms", default="A=sample,B=lite", help="NAME=sample|lite|v1|v2,...  lite/v2=variants/firebreak-*, v1=submission/")
ap.add_argument("--deadline-minutes", type=float, default=150, help="run_slice whole-run budget")
a = ap.parse_args()
OUTD = ROOT / "evalkit/notebook/out"; shutil.rmtree(OUTD, ignore_errors=True)
ds = OUTD / "dataset"; ds.mkdir(parents=True)
for f in ("run_slice.py", "run_task.py", "capture_shim.py", "guarded_parse.py"):
    shutil.copy(ROOT / "evalkit" / f, ds / f)
shutil.copy(ROOT / "evalkit/slices/slice40.json", ds / "slice40.json")
for zname, src in (("firebreak-lite", "variants/firebreak-lite"), ("firebreak-v1", "submission"), ("firebreak-v2", "variants/firebreak-v2")):
    subprocess.run([sys.executable, "-I", str(ROOT / "verify/build_zip.py"), str(ROOT / src), str(ds / f"{zname}.zip.bin")], check=True)
ARM_DIRS = {"sample": "sample", "lite": "/kaggle/working/firebreak_lite", "v1": "/kaggle/working/firebreak_v1",
            "v2": "/kaggle/working/firebreak_v2"}
arms_arg = ",".join(f"{n}={ARM_DIRS[k]}" for n, k in (x.split("=") for x in a.arms.split(",")))
FILE_DESC = {
    "run_slice.py": "Orchestrator: installs the wheelhouse, starts vLLM with the capture plugin, runs task jobs, saves output.",
    "run_task.py": "Runs one SWE task for one agent config in the official swegemma subprocess sandbox and writes its record.",
    "capture_shim.py": "vLLM tool-parser plugin: logs every raw completion the gemma4 parser sees (Wire Lab corpus C4).",
    "guarded_parse.py": "Copy of the pinned gemma4 array parser with a progress guard, so a would-be scorer hang is logged.",
    "slice40.json": "Seeded 40-task slice of the competition tasks used for capture runs (fastapi, rich, requests, httpx).",
    "firebreak-lite.zip.bin": "Firebreak submission #1 config (zip renamed .bin), byte-identical to dist/firebreak-lite.zip.",
    "firebreak-v1.zip.bin": "Firebreak skills config (safe-edit + airlock skills), measured in capture v7; not shipped.",
    "firebreak-v2.zip.bin": "Firebreak v2 config: firebreak-lite plus two prompt rules from captured-completion data.",
}
(ds / "dataset-metadata.json").write_text(json.dumps({
    "title": "firebreak-evalkit", "id": f"{a.owner}/firebreak-evalkit",
    "subtitle": "Scorer-faithful local eval and tool-call capture kit for Gemma 4 agents",
    "description": (
        "# Firebreak eval kit\n\n"
        "Scripts used by the Firebreak capture notebook for the Gemma 4 Developer Agent competition. They run "
        "agent configs on a slice of the competition tasks with the official swegemma harness and vLLM 0.19.1, "
        "and record every raw tool-call completion the gemma4 parser sees. That data measures how Gemma 4 31B "
        "actually formats tool calls: marker-quoted vs plain-quoted strings, array values, malformed keys.\n\n"
        "## Contents\n"
        "- Python scripts: orchestrator, per-task runner, vLLM parser plugin, guarded parser copy.\n"
        "- `slice40.json`: the seeded task slice.\n"
        "- `*.zip.bin`: agent configs (ordinary zip files renamed so Kaggle does not unpack them).\n\n"
        "## Use\n"
        "Attach to a GPU notebook (L4x4) together with the competition data, the host wheelhouse dataset and the "
        "Gemma 4 model, then run `run_slice.py --arms NAME=DIR --slice slice40.json`. Every run has a hard wall "
        "clock and fails fast if the model server dies.\n\n"
        "Source, evidence and decisions live in the Firebreak repository. Apache-2.0."),
    "licenses": [{"name": "apache-2.0"}],
    # only tags from Kaggle's fixed list are accepted; one invalid tag drops all of them
    "keywords": ["artificial intelligence", "computer science", "programming", "software", "nlp",
                 "deep learning", "gpu", "python", "text generation"],
    "expectedUpdateFrequency": "never",
    "resources": [{"path": f, "description": d} for f, d in FILE_DESC.items()],
}, indent=1))
code = f'''import glob, shutil, subprocess, sys, zipfile
from pathlib import Path
EK = Path(glob.glob("/kaggle/input/**/run_slice.py", recursive=True)[0]).parent
W = Path("/kaggle/working/evalkit"); shutil.copytree(EK, W, dirs_exist_ok=True)
zipfile.ZipFile(W / "firebreak-lite.zip.bin").extractall("/kaggle/working/firebreak_lite")
zipfile.ZipFile(W / "firebreak-v1.zip.bin").extractall("/kaggle/working/firebreak_v1")
zipfile.ZipFile(W / "firebreak-v2.zip.bin").extractall("/kaggle/working/firebreak_v2")
cmd = [sys.executable, str(W / "run_slice.py"), "--model", "{a.model}", "--arms", "{arms_arg}",
       "--slice", str(W / "slice40.json"), "--workers", "{a.workers}", "--limit", "{a.limit}",
       "--deadline-minutes", "{a.deadline_minutes}"]
print(" ".join(cmd), flush=True)
# Hard wall clock for the whole run, in its own process group, output to a file (not the kernel's pipe):
# run v5 sat 11.5 h after a startup error until Kaggle's 12 h cell limit.
import os, signal, time
OUT = Path("/kaggle/working/out"); OUT.mkdir(parents=True, exist_ok=True)
LIMIT = ({a.deadline_minutes} + 40) * 60
with open(OUT / "run_slice.log", "w") as f:
    p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time(); rc = None
    while rc is None and time.time() - t0 < LIMIT:
        time.sleep(30); rc = p.poll()
    if rc is None:
        print(f"[notebook] hard limit {{LIMIT}}s reached; killing run", flush=True)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try: os.killpg(p.pid, sig)
        except ProcessLookupError: pass
        time.sleep(5)
    for pat in ("vllm.entrypoints", "VLLM::", "vllm serve"):
        subprocess.run(["pkill", "-9", "-f", pat])
print("".join(open(OUT / "run_slice.log", errors="replace").readlines()[-150:]), flush=True)
print(open(OUT / "stage.json").read() if (OUT / "stage.json").exists() else "no stage.json", flush=True)
print(f"[notebook] run_slice rc={{rc}} after {{round(time.time() - t0)}}s", flush=True)
'''
nb = {"cells": [{"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": code.splitlines(True)}],
      "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
nbd = OUTD / "notebook"; nbd.mkdir()
(nbd / f"{a.slug}.ipynb").write_text(json.dumps(nb, indent=1))
(nbd / "kernel-metadata.json").write_text(json.dumps({
    "id": f"{a.owner}/{a.slug}", "title": a.slug, "code_file": f"{a.slug}.ipynb", "language": "python",
    "kernel_type": "notebook", "is_private": True, "enable_gpu": True, "enable_tpu": False, "enable_internet": False,
    "machine_shape": {"t4x2": "NvidiaTeslaT4", "l4x4": "NvidiaL4"}[a.accel],
    # the wheelhouse is cp312: pin the same image as the host's Getting Started notebook
    "docker_image": "gcr.io/kaggle-private-byod/python@sha256:37c64f7dd9c54116ecd1bcc88817c5469b88387388fade02bfa8bf3fc647d461",
    "dataset_sources": [f"{a.owner}/firebreak-evalkit", "metric/gemma-4-developer-agent-wheelhouse"],
    "competition_sources": ["gemma-4-developer-agent"], "kernel_sources": [],
    "model_sources": [f"google/gemma-4/Other/{a.model}/2"]}, indent=1) + "\n")
print("built", OUTD)
