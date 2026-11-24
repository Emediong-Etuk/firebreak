"""Expressibility (host-side): can EVERY gold fix be made through the safe_edit protocol alone?

For each of the 129 tasks, start from the real pre-image files and apply each gold hunk ONLY via
safe_edit commands (view to read hashes -> apply/insert/create with an escape-encoded payload
ending in @@END@@), exactly as the model would. Compare to the gold post-image (git apply).

Also builds Wire Lab corpus C5: every tool call this protocol needs (write_file for cmd/payload,
run_skill_script with only skill_name+file_path) and classifies each on the pinned wire.

Writes evidence/safe_edit/expressibility.json and evidence/wirelab/c5_summary.json.
(The in-sandbox version, graded by the official image, is `make verify` / verify/expressibility_sandbox.)
"""
from __future__ import annotations

import collections
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "wirelab"))
import gold  # noqa: E402
from parse_driver import Driver  # noqa: E402
from render import render_tool_call  # noqa: E402

SCRIPT = ROOT / "submission/skills/safe_edit/scripts/edit.py"


def encode(lines: list[str]) -> str:
    if not lines:  # deleting the whole range: the payload is just the sentinel
        return "@@END@@\n"
    return "\n".join(l.replace('"' * 3, "@@TQ@@").replace("'" * 3, "@@SQ3@@") for l in lines) + "\n@@END@@\n"


def load_edit(ws: Path):
    os.environ["FIREBREAK_WORKSPACE"] = str(ws)
    spec = importlib.util.spec_from_file_location("fb_edit_x", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def line_of(content: str, old_s: str) -> int:
    return content[: content.index(old_s)].count("\n") + 1


def main() -> int:
    tasks = [json.loads(l) for l in open(ROOT / "data/tasks.jsonl")]
    calls_c5: list[tuple[str, dict]] = []
    per_task, failures = {}, []
    for t in tasks:
        iid = t["instance_id"]
        fps = gold.parse_patch(t["patch"])
        pre = {fp.old: (gold.DATA / "preimages" / iid / "files" / fp.old).read_bytes()
               for fp in fps if not gold.is_new_file(fp)}
        post = gold.git_apply_postimage(pre, t["patch"])
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "workspace"
            (ws / ".git/info").mkdir(parents=True)
            for rel, b in pre.items():
                (ws / rel).parent.mkdir(parents=True, exist_ok=True)
                (ws / rel).write_bytes(b)
            m = load_edit(ws)
            (ws / ".firebreak").mkdir()
            ok, n_cmds, why = True, 0, ""

            def do(cmd: str, payload: str | None = None) -> str:
                nonlocal n_cmds
                n_cmds += 1
                calls_c5.append(("write_file", {"filepath": ".firebreak/cmd.txt", "content": cmd}))
                if payload is not None:
                    (ws / ".firebreak/payload.txt").write_text(payload)
                    calls_c5.append(("write_file", {"filepath": ".firebreak/payload.txt", "content": payload}))
                calls_c5.append(("run_skill_script", {"skill_name": "safe_edit", "file_path": "scripts/edit.py"}))
                return m.run(cmd)

            try:
                for fp in fps:
                    if gold.is_new_file(fp):
                        new_lines = [tx for tag, tx in fp.hunks[0].lines if tag == "+"]
                        do(f"create {fp.path}", encode(new_lines))
                        continue
                    if fp.new is None:
                        (ws / fp.old).unlink()
                        continue
                    for h in fp.hunks:
                        cur = (ws / fp.path).read_text(encoding="utf-8", errors="surrogateescape").replace("\r\n", "\n")
                        old_lines = [tx for tag, tx in h.lines if tag in " -"]
                        new_lines = [tx for tag, tx in h.lines if tag in " +"]
                        # locate the hunk: unique full-hunk match, else nearest to the header line
                        blob = "".join(x + "\n" for x in old_lines)
                        starts, i = [], cur.find(blob)
                        while i != -1:
                            starts.append(i); i = cur.find(blob, i + 1)
                        if not starts:
                            raise RuntimeError("hunk not found")
                        s = min((cur[:x].count("\n") + 1 for x in starts), key=lambda ln: abs(ln - h.old_start))
                        lines = cur.split("\n")
                        if old_lines:
                            e = s + len(old_lines) - 1
                            # read hashes the way the model does: view, then parse LINE|HH|
                            hh = {}
                            for lo in (s, e):  # view is capped at 120 lines: view each anchor's line
                                v = do(f"view {fp.path} {lo} {lo}")
                                hh.update({int(r.split("|")[0]): r.split("|")[1] for r in v.splitlines()[1:]})
                            do(f"apply {fp.path} {s}:{hh[s]} {e}:{hh[e]}", encode(new_lines))
                        else:
                            a = s - 1
                            anchor = f"{a}:{m.h2(lines[a - 1])}" if a >= 1 else "0:00"
                            do(f"insert {fp.path} {anchor}", encode(new_lines))
                for fp in fps:
                    if fp.new is None:
                        continue
                    got = (ws / fp.path).read_bytes()
                    want = post[fp.path]
                    if got.replace(b"\r\n", b"\n") != want.replace(b"\r\n", b"\n"):
                        ok, why = False, f"{fp.path}: result != gold post-image"
                        break
            except Exception as e:  # noqa: BLE001
                ok, why = False, f"{type(e).__name__}: {str(e)[:200]}"
        per_task[iid] = {"ok": ok, "commands": n_cmds, "why": why}
        if not ok:
            failures.append({"instance_id": iid, "why": why})

    # C5: classify every protocol call on the pinned wire
    d = Driver(timeout_s=0.5)
    cnt, bad = collections.Counter(), []
    risky = collections.Counter()
    for tool, args in calls_c5:
        r = d.classify(render_tool_call(tool, args), tool, args)
        cnt[r["cls"]] += 1
        if r["cls"] != "OK":
            bad.append({"tool": tool, "cls": r["cls"], "detail": str(r.get("detail"))[:120]})
        for v in args.values():
            if '"' * 3 in v or "'" * 3 in v:
                risky["arg_contains_triple_quote"] += 1
            if v.rstrip().endswith(('"', "'")):
                risky["arg_ends_with_quote"] += 1
    d.close()
    ev = ROOT / "evidence"
    (ev / "safe_edit").mkdir(parents=True, exist_ok=True)
    summary = {"tasks": len(tasks), "expressible": sum(v["ok"] for v in per_task.values()),
               "commands_total": sum(v["commands"] for v in per_task.values()), "failures": failures}
    (ev / "safe_edit/expressibility.json").write_text(json.dumps({**summary, "per_task": per_task}, indent=1) + "\n")
    c5 = {"calls": len(calls_c5), "classes": dict(cnt), "ok_rate": round(cnt["OK"] / len(calls_c5), 6),
          "f2_trigger_shapes_in_args": dict(risky), "non_ok_examples": bad[:10],
          "note": "faithful render of every call the safe_edit protocol needs for all 129 gold fixes"}
    (ev / "wirelab/c5_summary.json").write_text(json.dumps(c5, indent=1) + "\n")
    print(json.dumps(summary)[:600]); print(json.dumps(c5)[:600])
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
