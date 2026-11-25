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
import signal
import subprocess
import sys
import threading
import traceback
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("FIREBREAK_OUT", "/kaggle/working/out"))  # override for local tests
DATA = Path("/kaggle/input/competitions/gemma-4-developer-agent")


def stage(name: str, **kw) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "stage.json"
    d = json.loads(p.read_text()) if p.exists() else {"stages": []}
    d["stages"].append({"stage": name, "t": time.time(), **kw})
    p.write_text(json.dumps(d, indent=1, default=str) + "\n")
    print(f"[stage] {name} {kw}", flush=True)


FATAL = ("CUDA out of memory", "Engine core initialization failed", "EngineCore failed", "WorkerProc hit an exception",
         "RuntimeError: Worker failed")


def copy_server_log(server) -> None:
    lp = getattr(server, "log_path", None)
    if lp and Path(lp).exists():
        try:
            shutil.copy(lp, OUT / "vllm_server.log")
        except OSError:
            pass


def tail_server_log(server, stop: threading.Event) -> None:
    """Copy the vLLM log to OUT every 20 s and kill the server on a fatal engine error, so start() fails in
    seconds instead of waiting out startup_timeout with a dead engine behind a live API process (run v5)."""
    while not stop.wait(20):
        copy_server_log(server)
        p = OUT / "vllm_server.log"
        txt = p.read_text(errors="replace")[-200_000:] if p.exists() else ""
        hit = next((f for f in FATAL if f in txt), None)
        if hit and server.process is not None and server.process.poll() is None:
            stage("server_fatal", pattern=hit)
            kill_vllm(server.process.pid)
            return


def _descendants(pid: int) -> list[int]:
    kids: dict[int, list[int]] = {}
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                ppid = int((d / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            kids.setdefault(ppid, []).append(int(d.name))
    out, todo = [], [pid]
    while todo:
        for c in kids.get(todo.pop(), []):
            out.append(c); todo.append(c)
    return out


def kill_vllm(root_pid: int | None = None) -> None:
    """SIGKILL a process tree by pid (default: every descendant of this process), deepest first.
    vLLM's API server spawns EngineCore and TP workers; killing only the API server orphans them."""
    pids = _descendants(root_pid or os.getpid()) + ([root_pid] if root_pid else [])
    for pid in reversed(pids):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(2)


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
    ap.add_argument("--max-num-seqs", type=int, default=16)
    ap.add_argument("--deadline-minutes", type=float, default=300,
                    help="whole-run budget from start; jobs not started by then are skipped")
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
    # torch.cuda.is_bf16_supported() is True on T4 (counts emulation); vLLM requires compute capability >= 8.0
    bf16 = torch.cuda.is_available() and torch.cuda.get_device_capability(0)[0] >= 8
    cfg = VllmConfig(model=model_path, port=8000, host="127.0.0.1", tool_call_parser="gemma4", reasoning_parser="gemma4",
                     max_model_len=32768, dtype="bfloat16" if bf16 else "half", gpu_memory_utilization=0.90,  # T4: no bf16
                     enable_auto_tool_choice=True, enable_lora=False, scheduler_reserve_full_isl=False,
                     tensor_parallel_size=4 if ngpu >= 4 else (2 if ngpu >= 2 else 1), startup_timeout=60 * 20,
                     # max_num_seqs: the default 256 OOMs in vLLM's sampler warm-up for 31B on 4xL4 (run v5)
                     extra_args=["--tool-parser-plugin", str(HERE / "capture_shim.py"),
                                 "--max-num-seqs", str(a.max_num_seqs)])
    server = VllmServer(cfg)
    stop_tail = threading.Event()
    threading.Thread(target=tail_server_log, args=(server, stop_tail), daemon=True).start()
    try:
        server.start()  # raises ServerStartupError when the process dies or startup_timeout passes
    finally:
        copy_server_log(server)
    stage("server_up", gpus=ngpu, bf16=bf16, model_path=model_path, seconds=round(time.time() - t0))

    s = smoke("http://127.0.0.1:8000/v1", model_path)
    (OUT / "smoke.json").write_text(json.dumps(s, indent=1) + "\n")
    stage("smoke", ok=s["ok"])
    if not s["ok"]:
        stop_tail.set()
        copy_server_log(server)
        server.stop()
        raise SystemExit("smoke gate failed; aborting before any task (see out/smoke.json)")

    task_ids = json.loads(Path(a.slice).read_text())["task_ids"]
    if a.limit:
        task_ids = task_ids[: a.limit]
    jobs = [(t, n) for t in task_ids for n in arms]  # arms interleaved per task
    deadline = t0 + a.deadline_minutes * 60
    logs = OUT / "joblogs"; logs.mkdir(parents=True, exist_ok=True)

    def run(job):
        t, n = job
        if time.time() > deadline:  # whole-run budget spent: skip, so the run always ends and saves output
            return n, t, -2, "", "skipped: run deadline"
        cmd = [sys.executable, str(HERE / "run_task.py"), "--task", t, "--submission", str(arms[n]),
               "--out", str(OUT / "tasks" / n), "--model-path", model_path, "--alias", a.model,
               "--max-time-minutes", str(a.max_time_minutes), "--max-tool-calls", str(a.max_tool_calls),
               "--max-turns", str(a.max_turns), "--timeout-seconds", str(a.timeout_seconds)]
        # The job runs in its own process group with output to files, and the WHOLE group is killed when
        # it ends or times out. (subprocess.run(timeout=...) kills only the direct child, leaving orphaned
        # grandchildren such as pytest or sandboxed commands eating CPU for the rest of the run.)
        so_p, se_p = logs / f"{n}_{t}.out", logs / f"{n}_{t}.err"
        limit = min(a.max_time_minutes * 60 + 900, max(60, deadline + 600 - time.time()))
        with so_p.open("w") as so, se_p.open("w") as se:
            p = subprocess.Popen(cmd, stdout=so, stderr=se, stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                rc = p.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                rc = -9
            finally:
                try:
                    os.killpg(p.pid, signal.SIGKILL)  # reap any leftover grandchildren either way
                except ProcessLookupError:
                    pass
                if p.poll() is None:
                    p.wait()
        tail = lambda f, k: f.read_text(errors="replace")[-k:]  # noqa: E731
        return n, t, rc, tail(so_p, 400), ("runner timeout\n" if rc == -9 else "") + tail(se_p, 1500)
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex, (OUT / "runner.log").open("w") as log:
        for n, t, rc, so, se in ex.map(run, jobs):
            done += 1
            log.write(json.dumps({"arm": n, "task": t, "rc": rc, "stdout": so, "stderr": se}) + "\n"); log.flush()
            print(f"[{done}/{len(jobs)}] {n} {t} rc={rc} {so.strip()[-160:]}", flush=True)
    stage("tasks_done", jobs=len(jobs), seconds=round(time.time() - t0))
    stop_tail.set()
    copy_server_log(server)  # copy BEFORE stop(): stop() deletes the log file
    server.stop()
    stage("finished", seconds=round(time.time() - t0))
    return 0


if __name__ == "__main__":
    # Never hang on exit (run v5 sat 11.5 h after its startup error): print the error, kill every vLLM
    # process, flush, and hard-exit regardless of leftover threads or children.
    rc = 1
    try:
        rc = main() or 0
    except SystemExit as e:
        print(f"[exit] {e}", flush=True)
        rc = e.code if isinstance(e.code, int) else 1
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        rc = 1
    finally:
        try:
            stage("exit", rc=rc)
        except Exception:  # noqa: BLE001
            pass
        kill_vllm()
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(rc)
