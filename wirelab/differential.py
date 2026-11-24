"""Differential test: vendored parser vs the REAL pinned module.

"Real" = the unmodified vllm 0.19.1 `gemma4_tool_parser.py` (sha256-checked) executed as a module.
Only its vLLM-internal imports are stubbed: protocol dataclasses (ToolCall, FunctionCall, ...),
the logger, `make_tool_call_id`, and the ToolParser base, whose __init__/vocab are copied
from the pinned abstract_tool_parser.py. The parsing code itself — the module functions, the
regex, and Gemma4ToolParser.extract_tool_calls — runs verbatim.

For every case in the given corpora, both sides run in sandboxed workers; the test asserts
identical outcome class (RET/HANG/CRASH) and, on return, identical (tools_called, calls, content).
Usage: python -I wirelab/differential.py [--corpora c1,c2,c3] [--limit N]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import types
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINNED = HERE / "pinned"
sys.path.insert(0, str(PINNED))
sys.path.insert(0, str(HERE))


def load_real():
    src = PINNED / "gemma4_tool_parser.py.orig"
    want = json.loads((PINNED / "hashes.json").read_text())["gemma4_tool_parser.py"]
    assert hashlib.sha256(src.read_bytes()).hexdigest() == want, "pinned parser hash mismatch"

    @dataclass
    class FunctionCall:
        name: str
        arguments: str

    @dataclass
    class ToolCall:
        function: FunctionCall
        type: str = "function"
        id: str = "x"

    @dataclass
    class ExtractedToolCallInformation:
        tools_called: bool
        tool_calls: list
        content: str | None = None

    class ToolParser:  # __init__ and vocab copied from pinned abstract_tool_parser.py
        def __init__(self, tokenizer, tools=None):
            self.prev_tool_call_arr = []
            self.current_tool_id = -1
            self.current_tool_name_sent = False
            self.streamed_args_for_tool = []
            self.model_tokenizer = tokenizer
            self.tools = tools

        @cached_property
        def vocab(self):
            return self.model_tokenizer.get_vocab()

    stub = lambda name, **kw: sys.modules.setdefault(name, types.ModuleType(name)).__dict__.update(kw)  # noqa: E731
    stub("vllm"); stub("vllm.entrypoints"); stub("vllm.entrypoints.openai"); stub("vllm.tool_parsers")
    stub("vllm.entrypoints.chat_utils", make_tool_call_id=lambda *a, **k: "x")
    stub("vllm.entrypoints.openai.chat_completion"); stub("vllm.entrypoints.openai.chat_completion.protocol", ChatCompletionRequest=object)
    stub("vllm.entrypoints.openai.engine")
    stub("vllm.entrypoints.openai.engine.protocol", DeltaFunctionCall=object, DeltaMessage=object, DeltaToolCall=object,
         ExtractedToolCallInformation=ExtractedToolCallInformation, FunctionCall=FunctionCall, ToolCall=ToolCall)
    stub("vllm.entrypoints.openai.responses"); stub("vllm.entrypoints.openai.responses.protocol", ResponsesRequest=object)
    import logging
    stub("vllm.logger", init_logger=lambda n: logging.getLogger(n))
    stub("vllm.tokenizers", TokenizerLike=object)
    stub("vllm.tool_parsers.abstract_tool_parser", Tool=object, ToolParser=ToolParser)
    stub("vllm.tool_parsers.utils", find_common_prefix=lambda *a: "")
    from importlib.machinery import SourceFileLoader
    spec = importlib.util.spec_from_loader("gemma4_real", SourceFileLoader("gemma4_real", str(src)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Tok:
        def get_vocab(self):
            return {"<|tool_call>": 48, "<tool_call|>": 49, '<|"|>': 52}

    parser = mod.Gemma4ToolParser(Tok())

    def extract(text):
        info = parser.extract_tool_calls(text, request=None)
        return info.tools_called, [(c.function.name, c.function.arguments) for c in info.tool_calls], info.content
    return extract


def _worker_fn(which):
    if which == "real":
        return load_real()
    import gemma4_parser_pinned as p
    return p.extract_tool_calls


def main() -> int:
    import multiprocessing as mp
    import resource
    import signal
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpora", default="c1,c2,c3")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=0.5)
    a = ap.parse_args()

    def worker(conn, which):
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
        import parse_driver as pd
        signal.signal(signal.SIGALRM, pd._alarm)
        fn = _worker_fn(which)
        while True:
            try:
                text = conn.recv()
            except EOFError:
                return
            signal.setitimer(signal.ITIMER_REAL, a.timeout)
            try:
                res = ("RET", fn(text))
            except pd._Timeout:
                res = ("HANG", None)
            except MemoryError:
                res = ("CRASH", None)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            conn.send(res)
            if res[0] != "RET":
                return

    ctx = mp.get_context("fork")
    procs = {}

    def call(which, text):
        p = procs.get(which)
        if p is None or not p[0].is_alive():
            x, y = ctx.Pipe()
            pr = ctx.Process(target=worker, args=(y, which), daemon=True)
            pr.start()
            procs[which] = p = (pr, x)
        p[1].send(text)
        out = p[1].recv() if p[1].poll(a.timeout + 5) else ("HANG", None)
        if out[0] != "RET":
            p[0].kill(); p[0].join(); procs.pop(which)
        return out

    files = {"c1": "c1_fuzz/cases.jsonl", "c2": "c2_gold/calls.jsonl", "c3": "c3_deviations/cases.jsonl"}
    from render import render_tool_call
    n = same = 0
    mism = []
    per = {}
    for c in a.corpora.split(","):
        k = 0
        for line in open(HERE / "corpora" / files[c]):
            rec = json.loads(line)
            text = rec.get("text") or render_tool_call(rec["tool"], rec["args"])
            r1, r2 = call("real", text), call("vendored", text)
            n += 1; k += 1
            if r1 == r2:
                same += 1
            else:
                mism.append({"id": rec["id"], "real": str(r1)[:200], "vendored": str(r2)[:200]})
            if a.limit and k >= a.limit:
                break
        per[c] = k
    for p in procs.values():
        p[0].kill()
    out = {"cases": n, "identical": same, "mismatches": mism[:20], "per_corpus": per,
           "identical_rate": round(same / n, 6) if n else None,
           "method": "real = unmodified pinned gemma4_tool_parser.py executed with vLLM type/logger imports stubbed"}
    ev = HERE.parent / "evidence" / "wirelab"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "differential.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "mismatches"}))
    return 0 if same == n else 1


if __name__ == "__main__":
    sys.exit(main())
