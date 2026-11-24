"""vLLM tool-parser plugin for LOCAL evaluation runs (never part of a submission).

Loaded with `--tool-parser-plugin evalkit/capture_shim.py`. It re-registers the name "gemma4"
with a subclass of the pinned Gemma4ToolParser that:
  1. appends every raw completion that reaches the parser, and the parsed calls, to
     $FIREBREAK_CAPTURE (JSONL) -> Wire Lab corpus C4;
  2. replaces the module-level `_parse_gemma4_array` with a step-guarded copy: on a would-hang
     input it logs a SCORER_FATAL record (input + raw completion) and continues with upstream's
     fixed behaviour, so one fault does not freeze the local run. The scorer-equivalent count
     marks every task in flight at a SCORER_FATAL timestamp as FAILED.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from guarded_parse import make_guarded  # noqa: E402
from vllm.tool_parsers import gemma4_tool_parser as g  # noqa: E402
from vllm.tool_parsers.abstract_tool_parser import ToolParserManager  # noqa: E402

LOG = Path(os.environ.get("FIREBREAK_CAPTURE", "/kaggle/working/capture/capture.jsonl"))
LOG.parent.mkdir(parents=True, exist_ok=True)
_CTX = {"raw": None, "hint": None, "streaming": False}


def _write(rec: dict) -> None:
    rec["ts"] = time.time()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _on_fatal(arr_str: str) -> None:
    _write({"event": "SCORER_FATAL", "array_input": arr_str[:4000], "raw": (_CTX["raw"] or "")[:20000],
            "session_hint": _CTX["hint"], "streaming": _CTX["streaming"]})


g._parse_gemma4_array = make_guarded(g._parse_gemma4_args, g._parse_gemma4_value, _on_fatal)


def _hint(request) -> str | None:
    """'<sha(system)[:8]>|<sha(first user msg)[:12]>|<first 600 chars of it>': the system prompt
    identifies the arm, the first user message (problem statement) identifies the task."""
    try:
        sysmsg = ""
        for m in request.messages:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if role in ("system", "developer"):
                c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                sysmsg = str(c)
                break
        sys_h = hashlib.sha1(sysmsg.encode()).hexdigest()[:8]
        for m in request.messages:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if role == "user":
                c = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
                if isinstance(c, list):
                    c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
                c = str(c)
                return sys_h + "|" + hashlib.sha1(c.encode()).hexdigest()[:12] + "|" + c[:600]
    except Exception:  # noqa: BLE001
        return None
    return None


@ToolParserManager.register_module("gemma4", force=True)
class CapturingGemma4ToolParser(g.Gemma4ToolParser):
    def extract_tool_calls(self, model_output, request):
        _CTX.update(raw=model_output, hint=_hint(request), streaming=False)
        out = super().extract_tool_calls(model_output, request)
        try:
            calls = [(c.function.name, c.function.arguments) for c in out.tool_calls]
        except Exception:  # noqa: BLE001
            calls = None
        _write({"event": "extract", "raw": model_output, "tools_called": out.tools_called, "calls": calls,
                "session_hint": _CTX["hint"]})
        return out

    def extract_tool_calls_streaming(self, previous_text, current_text, delta_text, *a, **kw):
        _CTX.update(raw=current_text, streaming=True)
        req = kw.get("request") or (a[-1] if a else None)
        if _CTX.get("hint") is None or "<|tool_call>" not in previous_text:
            _CTX["hint"] = _hint(req) if req is not None else None
        out = super().extract_tool_calls_streaming(previous_text, current_text, delta_text, *a, **kw)
        if "<tool_call|>" in (delta_text or ""):
            _write({"event": "stream_call_end", "raw": current_text, "session_hint": _CTX["hint"]})
        return out
