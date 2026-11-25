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
ap.add_argument("--deadline-minutes", type=float, default=150, help="run_slice whole-run budget")
a = ap.parse_args()
OUTD = ROOT / "evalkit/notebook/out"; shutil.rmtree(OUTD, ignore_errors=True)
ds = OUTD / "dataset"; ds.mkdir(parents=True)
for f in ("run_slice.py", "run_task.py", "capture_shim.py", "guarded_parse.py"):
    shutil.copy(ROOT / "evalkit" / f, ds / f)
shutil.copy(ROOT / "evalkit/slices/slice40.json", ds / "slice40.json")
subprocess.run([sys.executable, "-I", str(ROOT / "verify/build_zip.py"), str(ROOT / "variants/firebreak-lite"), str(ds / "firebreak-lite.zip.bin")], check=True)
(ds / "dataset-metadata.json").write_text(json.dumps({"title": "firebreak-evalkit", "id": f"{a.owner}/firebreak-evalkit",
                                                       "licenses": [{"name": "apache-2.0"}]}, indent=1))
code = f'''import glob, shutil, subprocess, sys, zipfile
from pathlib import Path
EK = Path(glob.glob("/kaggle/input/**/run_slice.py", recursive=True)[0]).parent
W = Path("/kaggle/working/evalkit"); shutil.copytree(EK, W, dirs_exist_ok=True)
zipfile.ZipFile(W / "firebreak-lite.zip.bin").extractall("/kaggle/working/firebreak_lite")
cmd = [sys.executable, str(W / "run_slice.py"), "--model", "{a.model}", "--arms", "A=sample,B=/kaggle/working/firebreak_lite",
       "--slice", str(W / "slice40.json"), "--workers", "{a.workers}", "--limit", "{a.limit}",
       "--deadline-minutes", "{a.deadline_minutes}"]
print(" ".join(cmd), flush=True)
subprocess.run(cmd, check=True)
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
