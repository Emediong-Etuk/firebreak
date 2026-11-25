"""Build the public showcase notebook for the firebreak-evalkit dataset (CPU only, no internet).

Also adds to the dataset dir what the notebook reads:
  parser/   vLLM 0.19.1 gemma4 parser functions, vendored unmodified (Apache-2.0, NOTICE)
  results/  capture summaries, per-task records and raw tool calls for runs v6, v7, v8

Usage: build_showcase.py OWNER [--private]   (run evalkit/notebook/build_capture.py first)
Writes evalkit/notebook/out/showcase/{firebreak-evalkit-findings.ipynb,kernel-metadata.json}.
Local test: FIREBREAK_EVALKIT_DIR=evalkit/notebook/out/dataset python ... (see verify step in STATUS).
"""
import argparse
import gzip
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTD = ROOT / "evalkit/notebook/out"
DS = OUTD / "dataset"
RUNS = {"v6": "2026-11-25_31b_l4x4_v6", "v7": "2026-11-25_31b_l4x4_v7_skills", "v8": "2026-11-25_31b_l4x4_v8_v2"}
SLUG = "firebreak-evalkit-findings"  # code file name
KERNEL_ID = "gemma-4-tool-call-wire-1-160-captured-calls"  # Kaggle derived the slug from the title on first push

ap = argparse.ArgumentParser()
ap.add_argument("owner")
ap.add_argument("--private", action="store_true", help="keep the notebook private (draft)")
a = ap.parse_args()
assert (DS / "run_slice.py").exists(), "run build_capture.py first"

# --- dataset additions -------------------------------------------------------------------------
(DS / "parser").mkdir(exist_ok=True)
for f in ("gemma4_parser_pinned.py", "NOTICE", "hashes.json"):
    shutil.copy(ROOT / "wirelab/pinned" / f, DS / "parser" / f)
for tag, run in RUNS.items():
    d = DS / "results" / tag
    d.mkdir(parents=True, exist_ok=True)
    for f in ("summary.json", "tasks.jsonl"):
        shutil.copy(ROOT / "evidence/runs" / run / f, d / f)
    # shipped uncompressed: Kaggle decompresses .gz on upload, so this keeps local and Kaggle paths identical
    with gzip.open(ROOT / "evidence/runs" / run / "c4_calls.jsonl.gz", "rb") as src, open(d / "c4_calls.jsonl", "wb") as dst:
        shutil.copyfileobj(src, dst)
    (d / "c4_calls.jsonl.gz").unlink(missing_ok=True)
meta = json.loads((DS / "dataset-metadata.json").read_text())
extra = {
    "parser/gemma4_parser_pinned.py": "vLLM 0.19.1 gemma4 tool-parser functions, vendored unmodified (Apache-2.0, see parser/NOTICE).",
    "parser/NOTICE": "Attribution for the vendored vLLM parser code.",
    "parser/hashes.json": "sha256 of the vLLM source files the parser functions were extracted from.",
}
for tag, run in RUNS.items():
    extra[f"results/{tag}/summary.json"] = f"Capture {tag} ({run}): per-arm wire, tool and outcome statistics."
    extra[f"results/{tag}/tasks.jsonl"] = f"Capture {tag}: one record per (arm, task): resolved, tool calls, duration, trace stats."
    extra[f"results/{tag}/c4_calls.jsonl"] = f"Capture {tag}: every raw tool call the gemma4 parser saw, with per-value quoting style."
have = {r["path"] for r in meta.get("resources", [])}
meta["resources"] = [r for r in meta.get("resources", []) if not r["path"].endswith(".gz")] + \
    [{"path": p, "description": d} for p, d in extra.items() if p not in have]
meta.pop("keywords", None)  # a version upload rejects our 9 tags ("max category limit"); set them with a metadata update
(DS / "dataset-metadata.json").write_text(json.dumps(meta, indent=1))

# --- notebook ----------------------------------------------------------------------------------
cells = []
md = lambda s: cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n").splitlines(True)})  # noqa: E731
code = lambda s: cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],  # noqa: E731
                               "source": s.strip("\n").splitlines(True)})

md("""
# Gemma 4 31B on the tool-call wire: what 1,160 captured calls show

This notebook uses the **firebreak-evalkit** dataset to look at how Gemma 4 31B actually emits tool calls when it
runs as a coding agent under the competition's harness (swegemma, vLLM 0.19.1, `gemma4` tool parser).

Gemma 4 writes tool calls in its own wire format, not JSON:

```
<|tool_call>call:edit_file{filepath:<|"|>app/main.py<|"|>,old_string:<|"|>...<|"|>,new_string:<|"|>...<|"|>}<tool_call|>
```

Strings are delimited by the special token `<|"|>` and nothing is escaped. vLLM's `gemma4` parser turns that text back
into arguments. Two things can go wrong, and both matter for the score:

* **F1, a parser hang.** A plain-quoted value inside an **array** argument (`nodes:["a]"]`) sends the parser into an
  infinite loop. The parser runs synchronously inside vLLM's async server, so one bad completion freezes the server
  for every task that follows.
* **F2, a delimiter slip by the model.** If the model closes a string with the wrong character, the next argument is
  swallowed and ADK rejects the call ("mandatory input parameters are not present").

Below: a live CPU reproduction of F1 on the pinned parser code, then what 31B actually did across four agent configs
on the same 12 competition tasks.
""")

code("""
import glob, gzip, json, os, sys, subprocess, textwrap, zipfile, collections
from pathlib import Path
import pandas as pd
pd.set_option("display.max_colwidth", 120)
try:
    display
except NameError:  # outside Jupyter
    display = print

DS = Path(os.environ.get("FIREBREAK_EVALKIT_DIR") or Path(glob.glob("/kaggle/input/**/run_slice.py", recursive=True)[0]).parent)
print("dataset:", DS)
for p in sorted(DS.rglob("*")):
    if p.is_file() and p.suffix != ".pyc":
        print(f"  {str(p.relative_to(DS)):45} {p.stat().st_size:>8,} B")
""")

md("""
## 1. F1 live: one string hangs the parser

`parser/gemma4_parser_pinned.py` holds the parser functions from vLLM 0.19.1, copied unmodified (the source file's
sha256 is in `parser/hashes.json`). Each probe runs in a separate process with a 3-second limit, because a hang here
never returns.
""")

code("""
PROBE = textwrap.dedent('''
    import sys, json
    sys.path.insert(0, sys.argv[1])
    import gemma4_parser_pinned as P
    kind, s = sys.argv[2], sys.argv[3]
    out = P._parse_gemma4_array(s) if kind == "array" else P.extract_tool_calls(s)
    print(json.dumps(out, default=str)[:200])
''')

def probe(kind, text, seconds=3):
    try:
        r = subprocess.run([sys.executable, "-c", PROBE, str(DS / "parser"), kind, text],
                           capture_output=True, text=True, timeout=seconds)
        return "OK  -> " + (r.stdout.strip() or r.stderr.strip()[-120:])
    except subprocess.TimeoutExpired:
        return f"HANG (killed after {seconds}s)"

M = '<|"|>'
cases = [
    ("array", f'{M}--detail{M}, {M}List[int]{M}', "array, marker-quoted strings"),
    ("array", '"--detail", "List[int]"',              "array, plain-quoted strings"),
    ("array", '"a]"',                                  "array, minimal: one plain-quoted bracket"),
    ("call",  '<|tool_call>call:get_code_subgraph{nodes:["rich.pretty.Node", "List[int]"]}<tool_call|>',
              "full completion through extract_tool_calls"),
    ("call",  f'<|tool_call>call:run_command{{command:{M}pytest tests/test_x.py{M}}}<tool_call|>',
              "scalar-only call (control)"),
]
for kind, text, label in cases:
    print(f"{label:45} {probe(kind, text)}")
""")

md("""
The marker-quoted array and the scalar-only call parse instantly. Any plain-quoted value inside an array never returns.
On a server, that completion would hold the event loop and every later task would time out.

**Defence:** never declare array parameters (nothing then invites the model to emit `[`), and keep quoting
marker-only. Section 2 checks how far that holds with the real model.

## 2. What 31B actually emitted

Four agent configs ran the same 12 fastapi tasks from the competition data (seeded slice, `slice40.json`), on
4x L4 with the competition's model, harness and parser. A vLLM parser plugin logged every raw completion.

| arm | config |
|---|---|
| A | the competition's sample submission (has an array-typed tool, `get_code_subgraph`) |
| B | firebreak-lite: single agent, scalar-only tools, wire-safety prompt rules |
| C | firebreak-v1: B plus two skills (`run_skill_script` exposes array parameters) |
| D | firebreak-v2: B plus two prompt rules learned from run v6 |
""")

code("""
R = DS / "results"
summ = {t: json.loads((R / t / "summary.json").read_text()) for t in ("v6", "v7", "v8")}
arms = {"A sample": summ["v6"]["arms"]["A"], "B lite": summ["v6"]["arms"]["B"],
        "C skills": summ["v7"]["arms"]["C"], "D v2": summ["v8"]["arms"]["D"]}
rows = []
for name, a in arms.items():
    w = a["wire"]
    vals = sum(v for k, v in w.items() if k.startswith("value_"))
    rows.append({
        "arm": name, "tool calls": w.get("calls", 0),
        "marker-quoted values": w.get("value_marker", 0), "plain-quoted values": w.get("value_plain", 0),
        "array values": w.get("value_array", 0), "malformed keys": w.get("junk_key_calls", 0),
        "ADK-rejected calls": w.get("rejected_calls", 0),
        "skill calls w/ array args": w.get("skill_script_with_args", 0) + w.get("skill_script_with_positional_args", 0),
        "resolved /12": a["resolved_local_plain_grader"], "hit 5-min cap": a["tasks_hitting_time_cap"],
    })
df = pd.DataFrame(rows).set_index("arm")
display(df)
print("SCORER_FATAL events (would-be server hangs), all runs:", sum(s["scorer_fatal_events"] for s in summ.values()))
""")

md("""
**Reading it:**

* **Quoting is almost always right.** 1 plain-quoted value out of 1,971 top-level values; the rest are marker-quoted strings or bare numbers and booleans.
* **No arrays, ever.** The sample arm had an array-typed tool available and never called it. The skills arm called
  `run_skill_script` without ever filling its array parameters. So no run came near F1.
* **The real faults are smaller and different:** malformed keys ("JSON drift") and ADK-rejected calls (F2).
* **Resolve counts are 12-task numbers.** A difference of one task is noise.

## 3. The faults that did happen, verbatim
""")

code("""
def calls(tag):
    with open(R / tag / "c4_calls.jsonl") as f:
        return [json.loads(l) for l in f]
allc = [(t, c) for t in ("v6", "v7", "v8") for c in calls(t)]
print(f"{len(allc)} captured tool calls (the arm table counts 1,160; 3 calls could not be attributed to an arm)\\n")

print("JSON drift: the model switches to JSON syntax mid-call; the key becomes 'start_line\\"' and ADK silently drops it")
for t, c in allc:
    if 'start_line"' in c["text"]:
        print("  ", t, c["text"][:150]); break

print("\\nF2: new_string closed with a backtick instead of <|\\"|>, swallowing old_string -> 'mandatory input parameters are not present'")
for t, c in allc:
    if c["tool"] == "edit_file" and ",old_string:" in c["text"] and "`,old_string" in c["text"]:
        print("  ", t, c["text"][:420]); break

print("\\nLiteral backslash-n inside marker strings (swegemma's edit_file repairs these with its 'unescaped' strategy):")
n = sum(1 for _, c in allc if c["tool"] in ("edit_file", "write_file") and "\\\\n" in c["text"])
print(f"   {n} edit/write calls contain a literal \\\\n")
""")

md("""
F2 is not random. One task's code contains backticks: all four configs ran it, and in two of them the model made this
slip and then resent the **identical** rejected call 8 to 10 times. A prompt rule ("if rejected, shorten the anchor") did not
stop it (arm D), because the rejection names a missing parameter, not the cause.

## 4. Per-task outcomes
""")

code("""
def tasks(tag):
    return [json.loads(l) for l in open(R / tag / "tasks.jsonl")]
recs = {("A sample" if r["arm"] == "A" else "B lite" if r["arm"] == "B" else "C skills" if r["arm"] == "C" else "D v2", r["instance_id"]): r
        for t in ("v6", "v7", "v8") for r in tasks(t)}
ids = sorted({i for _, i in recs})
grid = pd.DataFrame({arm: ["resolved" if recs[(arm, i)]["resolved"] else "" for i in ids]
                     for arm in ("A sample", "B lite", "C skills", "D v2")}, index=ids)
display(grid)
dur = pd.DataFrame({arm: [round(recs[(arm, i)]["duration_seconds"] or 0) for i in ids]
                    for arm in ("A sample", "B lite", "C skills", "D v2")}, index=ids)
print("task wall time (s); the run cap was 300 s:")
display(dur)
""")

md("""
Time is the binding constraint: most tasks run into the 5-minute cap in every config, long before the 40-call limit.

## 5. The configs

The agent configs are ordinary zips (renamed `.zip.bin` so Kaggle doesn't unpack them). Here is what changed between
firebreak-lite and firebreak-v2:
""")

code("""
import difflib
def read(zname, member):
    with zipfile.ZipFile(DS / zname) as z:
        return z.read(member).decode()
for zname in ("firebreak-lite.zip.bin", "firebreak-v2.zip.bin"):
    with zipfile.ZipFile(DS / zname) as z:
        print(zname, "->", z.namelist())
print()
print(read("firebreak-v2.zip.bin", "agent.yaml"))
d = difflib.unified_diff(read("firebreak-lite.zip.bin", "prompts/system.md").splitlines(),
                         read("firebreak-v2.zip.bin", "prompts/system.md").splitlines(),
                         "lite/system.md", "v2/system.md", lineterm="", n=0)
print("\\n".join(d))
""")

md("""
## Takeaways

1. **Declare no array parameters.** F1 needs an array on the wire. With none declared, 31B produced none in 1,160 calls.
2. **Quoting drift is rare but real.** About 0.1% of values were plain-quoted, plus occasional JSON-style keys. Design
   tools so a dropped optional key is harmless.
3. **F2 is task-dependent and sticky.** Code with backticks or quotes at the edges of an edit can trap the model in a
   rejected-call loop. Prompt rules alone don't break it.
4. **Time, not calls, is the budget that binds** for a 31B agent on these tasks.

Data and scripts: this dataset (`results/`, `parser/`, the capture scripts). The parser code is vLLM's, Apache-2.0.
""")

nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
outn = OUTD / "showcase"
shutil.rmtree(outn, ignore_errors=True)
outn.mkdir(parents=True)
(outn / f"{SLUG}.ipynb").write_text(json.dumps(nb, indent=1))
(outn / "kernel-metadata.json").write_text(json.dumps({
    "id": f"{a.owner}/{KERNEL_ID}", "title": "Gemma 4 tool-call wire: 1,160 captured calls", "code_file": f"{SLUG}.ipynb",
    "language": "python", "kernel_type": "notebook", "is_private": bool(a.private), "enable_gpu": False,
    "enable_tpu": False, "enable_internet": False, "dataset_sources": [f"{a.owner}/firebreak-evalkit"],
    "competition_sources": [], "kernel_sources": [], "model_sources": [],
    "keywords": ["nlp", "python", "deep learning"]}, indent=1))
print("built", outn, "cells:", len(cells), "| dataset files added:", len(extra))
