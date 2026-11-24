"""Evaluate ONE task with the official swegemma Evaluator against an already-running vLLM server.

One task per process (F3: run_command is synchronous, so tasks sharing a process freeze together).
Mirrors the scorer settings documented in HARNESS_README §7.2 (compaction + context cache) and the
host notebook (subprocess sandbox on Kaggle). Writes <out>/<instance_id>.json with timestamps,
the result fields, and the patch.

Usage: run_task.py --task ID --submission DIR --out DIR --model-path PATH --alias NAME
                   [--max-time-minutes 5 --max-tool-calls 40 --max-turns 80 --timeout-seconds 180]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import traceback
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--submission", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--data", default="/kaggle/input/competitions/gemma-4-developer-agent")
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--alias", required=True)
    ap.add_argument("--api-base", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--max-time-minutes", type=float, default=5)
    ap.add_argument("--max-tool-calls", type=int, default=40)
    ap.add_argument("--max-turns", type=int, default=80)
    ap.add_argument("--timeout-seconds", type=int, default=180)
    a = ap.parse_args()

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rec = {"instance_id": a.task, "submission": a.submission, "t_start": time.time(), "pid": os.getpid()}
    try:
        import litellm
        from adk_submission import discover_adapters
        from adk_submission.registry import ModelRegistry
        from adk_submission.resolvers.generation import install_litellm_reasoning_patch
        from google.adk.agents.context_cache_config import ContextCacheConfig
        from google.adk.apps._configs import EventsCompactionConfig
        from google.adk.models.lite_llm import LiteLlm
        from swegemma.config import ALLOWED_ADAPTER_EXTENSIONS, EvalConfig, build_submission_limits
        from swegemma.evaluate import Evaluator
        from swegemma.models import load_tasks

        litellm.drop_params = True
        install_litellm_reasoning_patch()
        data = Path(a.data)
        task = next(t for t in load_tasks(data / "tasks.jsonl") if t.instance_id == a.task)
        models = ModelRegistry()
        models.register(a.alias, LiteLlm(model=f"openai/{a.model_path}", api_base=a.api_base, api_key="EMPTY"))
        limits, gen = build_submission_limits()
        cfg = EvalConfig(
            tasks_path=data / "tasks.jsonl", snapshots_dir=data / "snapshots", results_dir=out / "_swegemma",
            submission_dir=Path(a.submission), models=models, sandbox="subprocess",
            timeout_seconds=a.timeout_seconds, verification_timeout_seconds=300,
            max_time_minutes=a.max_time_minutes, max_tool_calls=a.max_tool_calls, max_turns=a.max_turns,
            limits=limits, generation_constraints=gen,
            adapter_manifest=discover_adapters(a.submission, adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS),
            context_cache_config=ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10),
            events_compaction_config=EventsCompactionConfig(compaction_interval=15, overlap_size=2,
                                                            token_threshold=14336, event_retention_size=5),
            graph_dir=str(data / "graphs"), embeddings_dir=str(data / "embeddings"), wheels_dir=data / "wheels",
            display_mode="quiet", task_ids=[a.task])
        res = asyncio.run(Evaluator(cfg).evaluate_task(task=task))
        d = res.model_dump() if hasattr(res, "model_dump") else dict(res.__dict__)
        rec.update({k: d.get(k) for k in ("resolved", "test_exit_code", "tool_calls", "duration_seconds", "error",
                                          "agent_error", "turns", "input_tokens", "output_tokens", "total_tokens")})
        rec["agent_patch"] = (getattr(res, "agent_patch", "") or "")
        rec["result_keys"] = sorted(d)
    except Exception as e:  # noqa: BLE001
        rec["runner_error"] = f"{type(e).__name__}: {e}"
        rec["traceback"] = traceback.format_exc()[-4000:]
    rec["t_end"] = time.time()
    (out / f"{a.task}.json").write_text(json.dumps(rec, indent=1, default=str) + "\n")
    print(json.dumps({k: rec.get(k) for k in ("instance_id", "resolved", "tool_calls", "runner_error")}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
