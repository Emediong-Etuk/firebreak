"""Compile a submission with the OFFICIAL harness compiler and audit what the model sees.

Checks (exit 1 on any failure):
  * official validate_directory() + compile_submission() succeed
  * exactly one declared model across all agents, and it is in ALLOWED_MODEL_NAMES
  * no symlinks, no files outside root, allowed extensions only, size < 3 GiB
  * D1 firewall: no `array` type anywhere in any tool schema sent to vLLM
    (unless --allow-array TOOL.PARAM is passed for a documented, mitigated exception)

Writes the full tool-surface report as JSON (stdout or --out).

Usage: python verify/check_submission.py SUBMISSION_DIR_OR_ZIP [--out FILE] [--allow-array run_skill_script.args ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ALLOWED_MODEL_NAMES = {
    "gemma-4-31b-it-qat-w4a16-ct",
    "gemma-4-12b-it-qat-w4a16-ct",
    "gemma-4-e4b-it-qat-w4a16-ct",
    "gemma-4-e2b-it-qat-w4a16-ct",
}


def find_arrays(schema: Any, path: str = "") -> list[str]:
    """Return JSON-pointer-ish paths of every array type in a schema."""
    hits: list[str] = []
    if isinstance(schema, dict):
        t = schema.get("type")
        if t == "array" or (isinstance(t, list) and "array" in t) or str(t).upper() == "ARRAY":
            hits.append(path or "/")
        for k, v in schema.items():
            hits.extend(find_arrays(v, f"{path}/{k}"))
    elif isinstance(schema, list):
        for i, v in enumerate(schema):
            hits.extend(find_arrays(v, f"{path}/{i}"))
    return hits


def walk_agents(agent: Any, seen: set[int] | None = None):
    seen = seen if seen is not None else set()
    if id(agent) in seen:
        return
    seen.add(id(agent))
    yield agent
    for sub in getattr(agent, "sub_agents", None) or []:
        yield from walk_agents(sub, seen)
    for tool in getattr(agent, "tools", None) or []:
        inner = getattr(tool, "agent", None)
        if inner is not None:
            yield from walk_agents(inner, seen)


async def agent_tool_params(agent: Any) -> list[dict]:
    """Resolve the agent's tools exactly as ADK does, and convert each declaration with
    ADK's own LiteLLM converter (what is sent to vLLM's /v1/chat/completions)."""
    from google.adk.models.lite_llm import _function_declaration_to_tool_param
    from google.adk.agents.readonly_context import ReadonlyContext

    out = []
    tools = await agent.canonical_tools(ReadonlyContext(_FakeInvocation(agent)))
    for t in tools:
        decl = t._get_declaration()
        if decl is None:
            continue
        param = _function_declaration_to_tool_param(decl)
        out.append(param)
    return out


class _FakeInvocation:
    """Minimal stand-in so canonical_tools() can resolve toolsets without a session."""

    def __init__(self, agent):
        self.agent = agent
        self.session = type("S", (), {"state": {}, "id": "audit", "app_name": "audit", "user_id": "audit"})()
        self.invocation_id = "audit"
        self.user_content = None
        self.branch = None
        self.run_config = None

    def __getattr__(self, name):  # anything else ADK may peek at
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--out")
    ap.add_argument("--allow-array", action="append", default=[],
                    help="TOOL.PARAM array exception that is mitigated elsewhere (documented in DECISIONS.md)")
    args = ap.parse_args()

    src = Path(args.submission)
    tmp = None
    if src.suffix == ".zip":
        tmp = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                p = Path(info.filename)
                if p.is_absolute() or ".." in p.parts:
                    print(f"FAIL zip member escapes root: {info.filename}")
                    return 1
            zf.extractall(tmp.name)
        sub = Path(tmp.name)
    else:
        sub = src

    failures: list[str] = []
    report: dict[str, Any] = {"submission": str(src)}
    if src.suffix == ".zip":
        report["zip_sha256"] = hashlib.sha256(src.read_bytes()).hexdigest()

    # --- file-level invariants -------------------------------------------------
    from swegemma.config import (ALLOWED_SUBMISSION_EXTENSIONS, MAX_SUBMISSION_SIZE_BYTES,
                                 build_submission_limits)
    total = 0
    for dirpath, dirnames, filenames in os.walk(sub, followlinks=False):
        for name in (*dirnames, *filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(sub)
            if p.is_symlink():
                failures.append(f"symlink: {rel}")
            if p.is_file():
                total += p.stat().st_size
                if p.suffix.lower() not in ALLOWED_SUBMISSION_EXTENSIONS:
                    failures.append(f"disallowed extension: {rel}")
    report["unpacked_bytes"] = total
    if total >= MAX_SUBMISSION_SIZE_BYTES:
        failures.append(f"size {total} >= {MAX_SUBMISSION_SIZE_BYTES}")

    # --- official validation + compile ----------------------------------------
    from adk_submission import validate_directory
    from adk_submission.discovery import discover_declared_models
    from adk_submission.registry import ModelRegistry
    from google.adk.models.lite_llm import LiteLlm
    from swegemma.context import SwegemmaContext
    from adk_submission import compile_submission

    limits, gen_constraints = build_submission_limits()
    try:
        validate_directory(sub, limits=limits)
    except Exception as e:  # noqa: BLE001
        failures.append(f"validate_directory: {type(e).__name__}: {e}")

    models = sorted(discover_declared_models(sub))
    report["declared_models"] = models
    if len(models) != 1:
        failures.append(f"expected exactly 1 declared model, got {models}")
    elif models[0] not in ALLOWED_MODEL_NAMES:
        failures.append(f"model not allowed: {models[0]}")

    registry = ModelRegistry()
    for m in models:
        registry.register(m, LiteLlm(model=f"openai/{m}", api_base="http://127.0.0.1:8000/v1", api_key="EMPTY"))
    # Adapter aliases resolve to the same base for audit purposes.
    from adk_submission import discover_adapters
    from swegemma.config import ALLOWED_ADAPTER_EXTENSIONS
    manifest = discover_adapters(str(sub), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)

    ctx = SwegemmaContext(problem_statement="audit", repo="audit/audit")
    from adk_eval_core.sandbox.base import AdkSandboxCodeExecutor
    executor = AdkSandboxCodeExecutor(sandbox=None, timeout_seconds=300)
    try:
        root = compile_submission(
            submission_dir=sub, tool_registry=ctx.create_tools(), model_registry=registry,
            code_executor=executor, limits=limits, generation_constraints=gen_constraints,
            adapter_manifest=manifest, adapter_resolver_fn=lambda base, info: base,
        )
    except Exception as e:  # noqa: BLE001
        failures.append(f"compile_submission: {type(e).__name__}: {e}")
        root = None

    agents_report = []
    if root is not None:
        import asyncio
        for agent in walk_agents(root):
            if not hasattr(agent, "canonical_tools"):
                agents_report.append({"name": agent.name, "class": type(agent).__name__, "tools": []})
                continue
            params = asyncio.run(agent_tool_params(agent))
            tools = []
            for p in params:
                fn = p.get("function", p)
                name = fn.get("name")
                arrays = find_arrays(fn.get("parameters", {}))
                tools.append({"name": name, "array_paths": arrays, "parameters": fn.get("parameters")})
                for a in arrays:
                    param = a.split("/")[2] if a.count("/") >= 2 else a
                    if f"{name}.{param}" not in args.allow_array:
                        failures.append(f"array param exposed to model: agent={agent.name} tool={name} path={a}")
            instr = getattr(agent, "instruction", "") or ""
            agents_report.append({
                "name": agent.name, "class": type(agent).__name__,
                "model": str(getattr(getattr(agent, "model", None), "model", getattr(agent, "model", ""))),
                "instruction_chars": len(instr) if isinstance(instr, str) else None,
                "tools": tools,
            })
    report["agents"] = agents_report
    report["allowed_array_exceptions"] = args.allow_array
    report["failures"] = failures
    report["ok"] = not failures

    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    summary = {a["name"]: [t["name"] + ("[ARRAY]" if t["array_paths"] else "") for t in a["tools"]] for a in agents_report}
    print(json.dumps(summary, indent=1))
    for f in failures:
        print("FAIL", f)
    print("OK" if not failures else f"{len(failures)} failure(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
