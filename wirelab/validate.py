"""Validate a parsed tool call the way ADK + the harness would, WITHOUT executing it.

Declarations are taken from the real swegemma tool callables (FunctionTool semantics:
unknown keys are dropped, missing mandatory params -> rejection) and from ADK's
SkillToolset tools (their own run_async argument checks), so "REJECTED" here means the
model would receive an error instead of the tool running.

Falls back to a frozen copy of the declarations (wirelab/pinned/tool_decls.json) when the
harness wheels are not installed, so verify-lite needs no Kaggle account.
"""
from __future__ import annotations

import inspect
import json
from functools import lru_cache
from pathlib import Path

DECLS = Path(__file__).resolve().parent / "pinned" / "tool_decls.json"

# ADK SkillToolset tools (google/adk/tools/skill_toolset.py, google-adk 1.36.1)
SKILL_TOOLS = {
    "list_skills": {"mandatory": [], "params": []},
    "load_skill": {"mandatory": ["skill_name"], "params": ["skill_name"]},
    "load_skill_resource": {"mandatory": ["skill_name", "file_path"], "params": ["skill_name", "file_path"]},
    "run_skill_script": {"mandatory": ["skill_name", "file_path"],
                         "params": ["skill_name", "file_path", "args", "short_options", "positional_args"]},
}


@lru_cache(maxsize=1)
def declarations() -> dict[str, dict]:
    try:
        from google.adk.tools.function_tool import FunctionTool
        from swegemma.context import SwegemmaContext
        tools = SwegemmaContext(problem_statement="x", repo="x/x").create_tools()
        out = {}
        for name, fn in tools.items():
            ft = FunctionTool(fn)
            params = [p for p in inspect.signature(fn).parameters if p != "tool_context"]
            out[name] = {"mandatory": list(ft._get_mandatory_args()), "params": params}
        out.update(SKILL_TOOLS)
        DECLS.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
        return out
    except ImportError:
        return json.loads(DECLS.read_text())


def validate(name: str, args: dict) -> tuple[bool, str]:
    """Return (accepted, reason)."""
    decls = declarations()
    if name not in decls:
        return False, f"unknown tool {name!r}"
    d = decls[name]
    kept = {k: v for k, v in args.items() if k in d["params"]}
    missing = [m for m in d["mandatory"] if m not in kept]
    if missing:
        return False, "mandatory input parameters are not present: " + ",".join(missing)
    if name == "run_skill_script":
        sa, so, pa = kept.get("args"), kept.get("short_options"), kept.get("positional_args")
        if not kept.get("skill_name") or not kept.get("file_path"):
            return False, "skill_name/file_path required"
        if sa is not None and not isinstance(sa, (dict, list)):
            return False, "'args' must be dict or list"
        if so is not None and not isinstance(so, dict):
            return False, "'short_options' must be dict"
        if pa is not None and not isinstance(pa, list):
            return False, "'positional_args' must be list"
    return True, "ok"
