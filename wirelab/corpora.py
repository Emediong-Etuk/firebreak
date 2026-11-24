"""Corpus generators.

C1  grammar fuzz      seeded random tool calls over a wire-hostile alphabet, in three emission
                      modes (faithful template render, all-plain-quoted, mixed)
C3  model deviations  operators that mimic ways a model can drift from the template, applied
                      to the C2 gold calls (realism notes per operator; cross-checked vs C4)

Every record: {"id", "corpus", "tool", "args", "text", "op"/"mode", ...}; "args" is the intent.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from render import render_tool_call

STRING_DELIM = '<|"|>'
HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- emission ----------
def _plain(v: str) -> str:
    return '"' + v + '"'


def _marker(v: str) -> str:
    return STRING_DELIM + v + STRING_DELIM


def fmt_value(v, mode: str, rng: random.Random | None = None) -> str:
    """Format one argument value. mode: faithful | plain | mixed."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if mode == "plain" or (mode == "mixed" and rng is not None and rng.random() < 0.5):
            return _plain(v)
        return _marker(v)
    if isinstance(v, list):
        return "[" + ",".join(fmt_value(x, mode, rng) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(f"{k}:{fmt_value(x, mode, rng)}" for k, x in sorted(v.items())) + "}"
    raise TypeError(type(v))


def emit(name: str, items: list[tuple[str, str]]) -> str:
    return "<|tool_call>call:" + name + "{" + ",".join(f"{k}:{v}" for k, v in items) + "}<tool_call|>"


# ---------------------------------------------------------------- C1 fuzz ------------
ATOMS = ['"', '"""', "'''", "'", "\\", "\\n", "[", "]", "{", "}", ",", ":", "\n", "\r\n", "\t", " ",
         "<|", "|>", '<|"|', '|"|>', "<tool_call|>", "<|tool_call>", "call:", "List[int]", "x", "def f():",
         "é", "→", "😀", "null", "true", "42", "3.5", "-", "--detail", "a.b.C", "test_x[case]", "#"]

DECL = json.loads((HERE / "pinned" / "tool_decls.json").read_text()) if (HERE / "pinned" / "tool_decls.json").exists() else {}
INT_PARAMS = {"start_line", "end_line", "max_neighbors", "k"}
BOOL_PARAMS = {"allow_multiple"}
LIST_PARAMS = {"nodes", "positional_args"}
DICT_PARAMS = {"short_options"}


def rand_str(rng: random.Random, max_atoms: int = 8) -> str:
    return "".join(rng.choice(ATOMS) for _ in range(rng.randint(1, max_atoms)))


def rand_args(rng: random.Random, tool: str) -> dict:
    d = DECL[tool]
    args = {}
    for p in d["params"]:
        if p not in d["mandatory"] and rng.random() < 0.5:
            continue
        if p in INT_PARAMS:
            args[p] = rng.randint(1, 500)
        elif p in BOOL_PARAMS:
            args[p] = rng.random() < 0.5
        elif p in LIST_PARAMS or (p == "args" and rng.random() < 0.5):
            args[p] = [rand_str(rng, 4) for _ in range(rng.randint(1, 3))]
        elif p in DICT_PARAMS or p == "args":
            args[p] = {"opt": rand_str(rng, 3)}
        else:
            args[p] = rand_str(rng)
    return args


def c1(n: int, seed: int = 20261124) -> list[dict]:
    rng = random.Random(seed)
    tools = sorted(DECL)
    out = []
    for i in range(n):
        tool = rng.choice(tools)
        args = rand_args(rng, tool)
        mode = rng.choices(["faithful", "plain", "mixed"], [0.6, 0.2, 0.2])[0]
        if mode == "faithful":
            text = render_tool_call(tool, args)
        else:
            text = emit(tool, [(k, fmt_value(v, mode, rng)) for k, v in sorted(args.items())])
        has_array = any(isinstance(v, list) for v in args.values())
        out.append({"id": f"c1-{i:06d}", "corpus": "c1", "tool": tool, "args": args, "text": text,
                    "mode": mode, "has_array": has_array})
    return out


# ---------------------------------------------------------------- C3 deviations -----
# Realism: how plausible each drift is for a model trained on the template (to be cross-checked
# against C4 captured completions; until then these are hypotheses, not frequencies).
OPERATORS = {
    "plain_one": "emits JSON-style \"...\" for one string arg (the longest) instead of the marker",
    "plain_all": "emits JSON-style \"...\" for every string arg",
    "json_escaped_all": "JSON habit: plain quotes AND backslash-escaped content quotes/newlines",
    "one_item_array": "wraps the longest string arg in a one-item array [<|\"|>v<|\"|>]",
    "one_item_array_plain": "wraps the longest string arg in a one-item plain array [\"v\"]",
    "stray_bracket": "emits a stray ']' after the last argument",
    "early_close_at_tq": "uses the marker as the first quote of a content triple quote (\"\"\" -> <|\"|>\"\")",
    "doubled_close": "doubles the closing marker of a value that ends in a quote character",
    "doubled_close_schema_order": "doubled closing marker after a quote-ending value, args in schema order (filepath last-but-not-first)",
    "missing_close": "omits the closing marker of the last argument",
    "backslash_quotes": "escapes content double quotes as \\\" inside marker strings",
    "crlf": "emits CRLF line endings inside string content",
}


def apply_op(op: str, tool: str, args: dict) -> str | None:
    """Return mutated text, or None if the operator does not apply to this call."""
    keys = sorted(args)
    strs = [k for k in keys if isinstance(args[k], str)]
    if not strs:
        return None
    longest = max(strs, key=lambda k: len(args[k]))

    def faithful_items(override=None):
        override = override or {}
        return [(k, override.get(k, fmt_value(args[k], "faithful"))) for k in keys]

    if op == "plain_one":
        return emit(tool, faithful_items({longest: _plain(args[longest])}))
    if op == "plain_all":
        return emit(tool, [(k, fmt_value(args[k], "plain")) for k in keys])
    if op == "json_escaped_all":
        return emit(tool, [(k, json.dumps(args[k], ensure_ascii=False) if isinstance(args[k], str)
                            else fmt_value(args[k], "faithful")) for k in keys])
    if op == "one_item_array":
        return emit(tool, faithful_items({longest: "[" + _marker(args[longest]) + "]"}))
    if op == "one_item_array_plain":
        return emit(tool, faithful_items({longest: "[" + _plain(args[longest]) + "]"}))
    if op == "stray_bracket":
        items = faithful_items()
        k, v = items[-1]
        items[-1] = (k, v + "]")
        return emit(tool, items)
    if op == "early_close_at_tq":
        tgt = [k for k in strs if '"""' in args[k]]
        if not tgt:
            return None
        k = tgt[0]
        return emit(tool, faithful_items({k: _marker(args[k].replace('"""', STRING_DELIM + '""', 1))}))
    if op == "doubled_close":
        tgt = [k for k in strs if args[k].rstrip().endswith(('"', "'"))]
        if not tgt:
            return None
        k = tgt[0]
        return emit(tool, faithful_items({k: _marker(args[k]) + STRING_DELIM}))
    if op == "doubled_close_schema_order":
        # model emits args in declaration order with filepath AFTER the content arg
        tgt = [k for k in strs if args[k].rstrip().endswith(('"', "'")) and k != "filepath"]
        if not tgt or "filepath" not in args:
            return None
        k = tgt[0]
        order = [k] + [x for x in keys if x != k]
        return emit(tool, [(x, _marker(args[x]) + (STRING_DELIM if x == k else "") if isinstance(args[x], str)
                            else fmt_value(args[x], "faithful")) for x in order])
    if op == "missing_close":
        items = faithful_items()
        k, v = items[-1]
        if not isinstance(args[k], str):
            return None
        items[-1] = (k, STRING_DELIM + args[k])
        return emit(tool, items)
    if op == "backslash_quotes":
        tgt = [k for k in strs if '"' in args[k]]
        if not tgt:
            return None
        return emit(tool, faithful_items({k: _marker(args[k].replace('"', '\\"')) for k in tgt}))
    if op == "crlf":
        tgt = [k for k in strs if "\n" in args[k]]
        if not tgt:
            return None
        return emit(tool, faithful_items({k: _marker(args[k].replace("\n", "\r\n")) for k in tgt}))
    raise KeyError(op)


def c3(c2_records: list[dict]) -> list[dict]:
    out = []
    for rec in c2_records:
        for op in OPERATORS:
            t = apply_op(op, rec["tool"], rec["args"])
            if t is None:
                continue
            out.append({"id": f"c3-{rec['id']}-{op}", "corpus": "c3", "tool": rec["tool"], "args": rec["args"],
                        "text": t, "op": op, "task": rec.get("task"), "kind": rec.get("kind"),
                        "src": rec["id"]})
    return out
