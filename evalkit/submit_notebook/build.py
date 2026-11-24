"""Generate the CPU-only Kaggle notebook that materializes a pinned submission.zip.

The notebook embeds the zip as base64, checks its sha256, and writes
/kaggle/working/submission.zip. No GPU, no internet, no inputs.
Usage: build.py ZIP OWNER [--slug firebreak-submit] [--out evalkit/submit_notebook/out]
"""
import argparse, base64, hashlib, json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("zip"); ap.add_argument("owner")
ap.add_argument("--slug", default="firebreak-submit")
ap.add_argument("--out", default="evalkit/submit_notebook/out")
a = ap.parse_args()
data = Path(a.zip).read_bytes()
sha = hashlib.sha256(data).hexdigest()
b64 = base64.b64encode(data).decode()
code = f'''import base64, hashlib, zipfile
from pathlib import Path
# Firebreak submission, pinned. sha256 of submission.zip:
EXPECTED_SHA256 = "{sha}"
ZIP_B64 = "{b64}"
data = base64.b64decode(ZIP_B64)
assert hashlib.sha256(data).hexdigest() == EXPECTED_SHA256, "zip hash mismatch"
out = Path("/kaggle/working/submission.zip")
out.write_bytes(data)
with zipfile.ZipFile(out) as z:
    assert z.testzip() is None and "agent.yaml" in z.namelist()
    for n in z.namelist():
        print(" ", n)
print("submission.zip", len(data), "bytes sha256", EXPECTED_SHA256)
'''
nb = {"cells": [{"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                 "source": code.splitlines(keepends=True)}],
      "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                   "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 5}
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
(out / f"{a.slug}.ipynb").write_text(json.dumps(nb, indent=1))
meta = {"id": f"{a.owner}/{a.slug}", "title": a.slug, "code_file": f"{a.slug}.ipynb",
        "language": "python", "kernel_type": "notebook", "is_private": True,
        "enable_gpu": False, "enable_tpu": False, "enable_internet": False,
        "dataset_sources": [], "kernel_sources": [], "model_sources": [],
        "competition_sources": ["gemma-4-developer-agent"]}
(out / "kernel-metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
print("notebook for", meta["id"], "zip sha256", sha)
