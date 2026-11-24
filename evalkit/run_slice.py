"""Kaggle-notebook orchestrator for a local capture run (Phase 3 / Wire Lab C4).

Stages (each writes /kaggle/working/out/stage.json so a failed run still tells us how far it got):
  1. install the official wheelhouse (same recipe as the host's Getting Started notebook)
  2. build each arm's submission dir for the chosen model (model swapped, LoRA adapters removed)
  3. start ONE vLLM server with the capture plugin (evalkit/capture_shim.py)
  4. smoke gate: one tool-call request; abort before any task if output is empty/garbage
  5. run every (task, arm) pair, one task per process, N at a time, arms interleaved
  6. copy capture log, per-task records, traces and server log to /kaggle/working/out

Usage (notebook cell): python run_slice.py --model gemma-4-e4b-it-qat-w4a16-ct --arms A=...,B=... --slice slice40.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path("/kaggle/working/out")
DATA = Path("/kaggle/input/competitions/gemma-4-developer-agent")


def stage(name: str, **kw) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "stage.json"
    d = json.loads(p.read_text()) if p.exists() else {"stages": []}
    d["stages"].append({"stage": name, "t": time.time(), **kw})
    p.write_text(json.dumps(d, indent=1, default=str) + "\n")
    print(f"[stage] {name} {kw}", flush=True)


def find_input(pattern: str) -> Path:
    hits = glob.glob(f"/kaggle/input/**/{pattern}", recursive=True)
    if not hits:
        raise SystemExit(f"input not found: {pattern}")
    return Path(hits[0])


def install_wheelhouse() -> None:
    wh = find_input("adk_submission-*.whl").parent
    for pth in glob.glob("/usr/local/lib/python*/*-packages/*cutlass*.pth"):
        try:
            os.unlink(pth)
        except OSError:
            pass
    tmp = Path("/tmp/wheelhouse"); tmp.mkdir(exist_ok=True)
    for w in wh.glob("*.whl"):
        if "cutlass" in w.name.lower():
            continue
        name = w.name.replace("cu128", "+cu128") if ("cu128" in w.name and "+" not in w.name) else w.name
        if not (tmp / name).exists():
            os.symlink(w, tmp / name)
    wheels = sorted(str(w) for w in tmp.glob("*.whl"))
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall", *wheels], check=True)


def build_arm(src: Path, dest: Path, model: str) -> Path:
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
    for y in [*dest.glob("*.yaml"), *dest.glob("sub_agents/*.yaml")]:
        t = y.read_text()
        t = re.sub(r"^(\s*model:\s*).*$", rf"\g<1>{model}", t, flags=re.M)
        t = re.sub(r"^\s*adapter:\s*.*\n?", "", t, flags=re.M)
        y.write_text(t)
    shutil.rmtree(dest / "adapters", ignore_errors=True)
    return dest


def smoke(api_base: str, model_path: str) -> dict:
    import requests
    body = {"model": model_path, "max_tokens": 512, "temperature": 0,
            "messages": [{"role": "user", "content": "Use the run_command tool to run: echo firebreak"}],
            "tools": [{"type": "function", "function": {"name": "run_command", "description": "Run a shell command.",
                       "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                                      "required": ["command"]}}}]}
    r = requests.post(f"{api_base}/chat/completions", json=body, timeout=300)
    j = r.json()
    msg = j.get("choices", [{}])[0].get("message", {})
    text = (msg.get("content") or "") + json.dumps(msg.get("tool_calls") or [])
    ok = bool(msg.get("tool_calls")) or (len(text.strip()) > 10 and "nan" not in text.lower()[:50])
    return {"ok": ok, "status": r.status_code, "message": msg, "usage": j.get("usage")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemma-4-e4b-it-qat-w4a16-ct")
    ap.add_argument("--arms", required=True, help="NAME=DIR,NAME=DIR (DIR may be 'sample')")
    ap.add_argument("--slice", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-time-minutes", type=float, default=5)
    ap.add_argument("--max-tool-calls", type=int, default=40)
    ap.add_argument("--max-turns", type=int, default=80)
    ap.add_argument("--timeout-seconds", type=int, default=180)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)

    install_wheelhouse(); stage("installed")
    import torch
    from adk_submission import VllmConfig, VllmServer

    arms = {}
    for spec in a.arms.split(","):
        name, d = spec.split("=")
        src = DATA / "sample_submission" if d == "sample" else Path(d)
        arms[name] = build_arm(src, Path(f"/kaggle/working/arms/{name}"), a.model)
    stage("arms", arms={k: str(v) for k, v in arms.items()})

    model_path = str(find_input(f"{a.model}/2/config.json").parent) if glob.glob(
        f"/kaggle/input/**/{a.model}/2/config.json", recursive=True) else str(find_input("config.json").parent)
    capture = OUT / "capture.jsonl"
    os.environ["FIREBREAK_CAPTURE"] = str(capture)
    ngpu = torch.cuda.device_count()
    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    cfg = VllmConfig(model=model_path, port=8000, host="127.0.0.1", tool_call_parser="gemma4", reasoning_parser="gemma4",
                     max_model_len=32768, dtype="bfloat16" if bf16 else "half", gpu_memory_utilization=0.90,  # T4: no bf16
                     enable_auto_tool_choice=True, enable_lora=False, scheduler_reserve_full_isl=False,
                     tensor_parallel_size=4 if ngpu >= 4 else (2 if ngpu >= 2 else 1), startup_timeout=60 * 25,
                     extra_args=["--tool-parser-plugin", str(HERE / "capture_shim.py")])
    server = VllmServer(cfg)
    try:
        server.start()
    finally:
        for lp in [getattr(server, "log_path", None)]:
            if lp and Path(lp).exists():
                shutil.copy(lp, OUT / "vllm_server.log")
    stage("server_up", gpus=ngpu, bf16=bf16, model_path=model_path, seconds=round(time.time() - t0))

    s = smoke("http://127.0.0.1:8000/v1", model_path)
    (OUT / "smoke.json").write_text(json.dumps(s, indent=1) + "\n")
    stage("smoke", ok=s["ok"])
    if not s["ok"]:
        lp = getattr(server, "log_path", None)
        if lp and Path(lp).exists():
            shutil.copy(lp, OUT / "vllm_server.log")
        server.stop()
        raise SystemExit("smoke gate failed; aborting before any task (see out/smoke.json)")

    task_ids = json.loads(Path(a.slice).read_text())["task_ids"]
    if a.limit:
        task_ids = task_ids[: a.limit]
    jobs = [(t, n) for t in task_ids for n in arms]  # arms interleaved per task
    def run(job):
        t, n = job
        cmd = [sys.executable, str(HERE / "run_task.py"), "--task", t, "--submission", str(arms[n]),
               "--out", str(OUT / "tasks" / n), "--model-path", model_path, "--alias", a.model,
               "--max-time-minutes", str(a.max_time_minutes), "--max-tool-calls", str(a.max_tool_calls),
               "--max-turns", str(a.max_turns), "--timeout-seconds", str(a.timeout_seconds)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.max_time_minutes * 60 + 900)
            return n, t, p.returncode, p.stdout[-400:], p.stderr[-1500:]
        except subprocess.TimeoutExpired:
            return n, t, -9, "", "runner timeout"
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex, (OUT / "runner.log").open("w") as log:
        for n, t, rc, so, se in ex.map(run, jobs):
            done += 1
            log.write(json.dumps({"arm": n, "task": t, "rc": rc, "stdout": so, "stderr": se}) + "\n"); log.flush()
            print(f"[{done}/{len(jobs)}] {n} {t} rc={rc} {so.strip()[-160:]}", flush=True)
    stage("tasks_done", jobs=len(jobs), seconds=round(time.time() - t0))
    lp = getattr(server, "log_path", None)
    if lp and Path(lp).exists():  # copy BEFORE stop(): stop() deletes the log file
        shutil.copy(lp, OUT / "vllm_server.log")
    server.stop()
    stage("finished", seconds=round(time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
