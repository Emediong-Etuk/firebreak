"""Hand-written, realistic Python/CLI shapes run through the pinned parser in both quoting styles.
Shows which REAL code shapes hang/mangle when the model plain-quotes a scalar string arg (F1b).
Writes evidence/wirelab/realistic_probes.json."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from corpora import emit, _marker, _plain  # noqa: E402
from parse_driver import Driver  # noqa: E402

PROBES = [
    "def f(a, b: List[int]) -> None:", 'x = {"k": [1, 2]}', "items = data[:2]", 'return {"a": ["b"], "c": 1}',
    'opts = {"a": 1, "ids": ["x[0]"]}', 'm = {"k": 1, "types": ["List[int]"]}',
    'pytest.main(["-q", "tests/t.py::test_a[case]"])', 'def f(a, b: list[str] = ["x[1]"]):',
    'python -m pytest "tests/test_x.py::test_a[case-1]" -q', 'def f():\n    """Doc."""\n    return 1',
]
d = Driver(timeout_s=0.5)
rows = []
for p in PROBES:
    for tool, key in (("run_command", "command"), ("edit_file", "old_string")):
        for style, q in (("marker", _marker), ("plain", _plain)):
            items = [(key, q(p))] if tool == "run_command" else \
                [("filepath", _marker("a.py")), ("new_string", _marker("x")), ("old_string", q(p))]
            intent = {key: p} if tool == "run_command" else {"filepath": "a.py", "new_string": "x", "old_string": p}
            r = d.classify(emit(tool, items), tool, intent)
            rows.append({"code": p, "tool": tool, "quoting": style, "cls": r["cls"]})
d.close()
out = {"rows": rows, "summary": {s: {c: sum(1 for r in rows if r["quoting"] == s and r["cls"] == c)
                                     for c in ("OK", "MANGLED", "REJECTED", "HANG")} for s in ("marker", "plain")},
       "hang_shapes_plain": sorted({r["code"] for r in rows if r["cls"] == "HANG"})}
p = HERE.parent / "evidence" / "wirelab" / "realistic_probes.json"
p.write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out["summary"]), out["hang_shapes_plain"])
