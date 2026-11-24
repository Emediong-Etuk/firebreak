"""Minimal reproducers for every fault class, shrunk by Hypothesis `find`.

Each search fixes an emission shape and lets Hypothesis shrink the *content* to the smallest
string that still produces the target class on the pinned parser. Results go to
evidence/wirelab/reproducers/<name>.json (one-line description + exact text + outcome).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from hypothesis import HealthCheck, find, settings
from hypothesis import strategies as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corpora import ATOMS, STRING_DELIM, emit, fmt_value  # noqa: E402
from parse_driver import Driver  # noqa: E402
from render import render_tool_call  # noqa: E402

OUT = HERE.parent / "evidence" / "wirelab" / "reproducers"
content = st.lists(st.sampled_from(ATOMS), min_size=1, max_size=8).map("".join)
D = Driver(timeout_s=0.3)


def cls_of(text, tool, args):
    return D.classify(text, tool, args)


SEARCHES = [
    ("hang_declared_array_plain", "F1 via a declared array param (get_code_subgraph.nodes) emitted with plain quotes",
     lambda v: (emit("get_code_subgraph", [("nodes", fmt_value([v], "plain"))]), "get_code_subgraph", {"nodes": [v]}), "HANG"),
    ("hang_scalar_wrapped_in_plain_array", "F1 with NO array params declared: model wraps a string arg as [\"...\"]",
     lambda v: (emit("run_command", [("command", "[" + '"' + v + '"' + "]")]), "run_command", {"command": v}), "HANG"),
    ("faithful_scalar_mangled", "faithful template render of a scalar string that still mangles (content contains wire tokens)",
     lambda v: (render_tool_call("run_command", {"command": v}), "run_command", {"command": v}), "MANGLED"),
    ("plain_scalar_rejected", "plain-quoted content arg whose text swallows the following mandatory key",
     lambda v: (emit("edit_file", [("new_string", '"x"'), ("old_string", '"' + v + '"'), ("filepath", '"a.py"')]),
                "edit_file", {"filepath": "a.py", "old_string": v, "new_string": "x"}), "REJECTED"),
    ("early_close_rejected", "F2: marker used as a content quote inside edit_file, schema order -> mandatory param lost",
     lambda v: (emit("edit_file", [("old_string", STRING_DELIM + v + STRING_DELIM + STRING_DELIM),
                                   ("filepath", STRING_DELIM + "a.py" + STRING_DELIM),
                                   ("new_string", STRING_DELIM + "x" + STRING_DELIM)]),
                "edit_file", {"filepath": "a.py", "old_string": v, "new_string": "x"}), "REJECTED"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    found = {}
    for name, desc, build, target in SEARCHES:
        try:
            v = find(content, lambda v: cls_of(*build(v))["cls"] == target,
                     settings=settings(max_examples=3000, database=None, derandomize=True, deadline=None,
                                       suppress_health_check=list(HealthCheck)))
        except Exception as e:  # noqa: BLE001  (NoSuchExample etc.)
            found[name] = None
            (OUT / f"{name}.json").write_text(json.dumps({"description": desc, "target": target,
                                                          "found": False, "error": type(e).__name__}, indent=1) + "\n")
            print(f"{name:36} not found ({type(e).__name__})")
            continue
        text, tool, args = build(v)
        r = cls_of(text, tool, args)
        rec = {"description": desc, "target": target, "found": True, "minimal_content": v, "tool": tool, "args": args,
               "text": text, "outcome": r["cls"], "detail": str(r.get("detail"))[:300],
               "command": f"python -I wirelab/replay.py {OUT.name}/{name}.json"}
        (OUT / f"{name}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        found[name] = v
        print(f"{name:36} {r['cls']:8} minimal content={v!r}")
    D.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
