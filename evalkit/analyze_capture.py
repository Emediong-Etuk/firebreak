"""Analyze a capture run's output dir (downloaded /kaggle/working/out) -> evidence/runs/<name>/.

Measures, per arm, from the RAW completions the parser saw (Wire Lab corpus C4):
  * emission style of every top-level argument value: marker <|"|> / plain " / array [ / object { / bare
  * calls with an array value on the wire, doubled markers, junk keys, ADK-rejected calls
  * SCORER_FATAL events and the scorer-equivalent outcome (every task in flight at a fatal -> FAILED)
and, from per-task records, resolve/tool-call stats.

Arm attribution: session_hint = sha(system)[:8] | sha(first user msg)[:12] | text. The task comes
from the problem statement; the arm from the system-prompt hash (mapped by majority vote against the
time windows of each arm's job for that task).

Usage: python -I evalkit/analyze_capture.py OUT_DIR RUN_NAME [MAX_TIME_MINUTES=5] [MAX_TOOL_CALLS=40]
"""
from __future__ import annotations

import collections
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "wirelab")); sys.path.insert(0, str(ROOT / "wirelab" / "pinned"))
from validate import validate  # noqa: E402

D = '<|"|>'
CALL = re.compile(r"<\|tool_call>call:([\w\-\.]+)\{(.*?)\}<tool_call\|>", re.S)


def value_styles(args: str) -> list[tuple[str, str]]:
    """Top-level (key, style) pairs, scanning outside marker strings."""
    out, i, n = [], 0, len(args)
    while i < n:
        while i < n and args[i] in " ,\n\t":
            i += 1
        j = args.find(":", i)
        if j < 0:
            break
        key = args[i:j].strip()
        i = j + 1
        while i < n and args[i] in " \n\t":
            i += 1
        if args.startswith(D, i):
            style = "marker"
            e = args.find(D, i + len(D))
            i = n if e < 0 else e + len(D)
        elif i < n and args[i] in "[{":
            style = "array" if args[i] == "[" else "object"
            close, depth = {"[": "]", "{": "}"}[args[i]], 0
            while i < n:
                if args.startswith(D, i):
                    e = args.find(D, i + len(D)); i = n if e < 0 else e + len(D); continue
                if args[i] in "[{":
                    depth += 1
                elif args[i] in "]}":
                    depth -= 1
                    if depth == 0:
                        i += 1; break
                i += 1
        elif i < n and args[i] == '"':
            style = "plain"
            while i < n and args[i] not in ",}":
                i += 1
        else:
            style = "bare"
            while i < n and args[i] not in ",}]":
                i += 1
            if i < n and args[i] == "]":
                i += 1
        out.append((key, style))
    return out


REJECT_MARKERS = ("mandatory input parameters are not present", "is not found", "not found in", "INVALID_ARGUMENTS")


def trace_metrics(path: Path) -> dict:
    """Per-task metrics from a swegemma SessionTrace (ATIF json)."""
    from adk_eval_core.tracing.trace import SessionTrace
    tr = SessionTrace.from_json(path)
    calls, by_tool, errors, rejected = [], collections.Counter(), 0, 0
    pending = []
    for e in tr.entries:
        if e.event_type == "tool_call":
            by_tool[e.tool_name] += 1
            pending.append((e.tool_name, json.dumps(e.tool_args or {}, sort_keys=True)))
        elif e.event_type == "tool_response":
            res = e.tool_result or ""
            call = pending.pop(0) if pending else (e.tool_name, "")
            is_rej = any(m in res for m in REJECT_MARKERS)
            is_err = is_rej or '"status": "error"' in res or "'status': 'error'" in res
            errors += is_err; rejected += is_rej
            calls.append((call, is_err, is_rej))
    # longest run of identical consecutive calls that each failed (retry storm)
    best = run = 0
    prev = None
    for call, is_err, _ in calls:
        run = run + 1 if (is_err and call == prev) else (1 if is_err else 0)
        best = max(best, run)
        prev = call if is_err else None
    # edit strategies (swegemma repairs literal \\n escapes via its 'unescaped' strategy) and error types
    strategies, err_types = collections.Counter(), collections.Counter()
    for e in tr.entries:
        if e.event_type != "tool_response":
            continue
        res = e.tool_result or ""
        if e.tool_name == "edit_file":
            m = re.search(r"strategy['\"]?\s*[:=]\s*['\"]?(\w+)", res)
            if m:
                strategies[m.group(1)] += 1
        m = re.search(r"error_type['\"]?\s*[:=]\s*['\"](\w+)", res)
        if m:
            msg = re.search(r"error_message['\"]?\s*[:=]\s*['\"]([^'\"]{0,60})", res)
            err_types[f"{e.tool_name}:{m.group(1)}:{msg.group(1) if msg else ''}"[:110]] += 1
    return {"tool_calls_by_type": dict(by_tool), "tool_errors": errors, "rejected_calls": rejected,
            "max_identical_failed_retry_run": best, "edit_strategies": dict(strategies),
            "error_types": dict(err_types)}


def main() -> int:
    out_dir, name = Path(sys.argv[1]), sys.argv[2]
    time_cap_s = float(sys.argv[3]) * 60 if len(sys.argv) > 3 else 300.0  # run's --max-time-minutes
    call_cap = int(sys.argv[4]) if len(sys.argv) > 4 else 40
    ev = ROOT / "evidence" / "runs" / name
    ev.mkdir(parents=True, exist_ok=True)
    tasks = {json.loads(l)["instance_id"]: json.loads(l) for l in open(ROOT / "data/tasks.jsonl")}
    recs = [json.loads(l) for l in open(out_dir / "capture.jsonl")] if (out_dir / "capture.jsonl").exists() else []
    jobs = []
    for arm_dir in sorted((out_dir / "tasks").glob("*")):
        for f in arm_dir.glob("*.json"):
            r = json.loads(f.read_text()); r["arm"] = arm_dir.name
            tp = next((arm_dir / "_swegemma").rglob(f"trace_{r['instance_id']}.json"), None) if (arm_dir / "_swegemma").exists() else None
            if tp is not None:
                try:
                    r["trace"] = trace_metrics(tp)
                except Exception as e:  # noqa: BLE001
                    r["trace"] = {"error": f"{type(e).__name__}: {e}"}
            jobs.append(r)

    def task_of(hint: str | None) -> str | None:
        if not hint:
            return None
        text = hint.split("|", 2)[-1]
        for iid, t in tasks.items():
            ps = t["problem_statement"].strip()[:120]
            if ps and ps in text:
                return iid
        return None

    # arm attribution via system hash: vote using job time windows
    votes = collections.defaultdict(collections.Counter)
    for r in recs:
        h = (r.get("session_hint") or "").split("|")
        if len(h) < 3:
            continue
        iid = task_of(r.get("session_hint"))
        for j in jobs:
            if j["instance_id"] == iid and j["t_start"] <= r["ts"] <= j["t_end"]:
                votes[h[0]][j["arm"]] += 1
    sys2arm = {h: c.most_common(1)[0][0] for h, c in votes.items() if c}

    per = collections.defaultdict(lambda: collections.Counter())
    examples = collections.defaultdict(list)
    c4 = []
    fatals = [r for r in recs if r["event"] == "SCORER_FATAL"]
    for r in recs:
        if r["event"] not in ("extract", "stream_call_end"):
            continue
        hint = r.get("session_hint") or ""
        arm = sys2arm.get(hint.split("|")[0], "?")
        raw = r.get("raw") or ""
        st = per[arm]
        st["completions"] += 1
        if "<|tool_call>" in raw and not CALL.search(raw):
            st["unclosed_or_unmatched_call"] += 1
        for fn, args in CALL.findall(raw):
            st["calls"] += 1
            styles = value_styles(args)
            for k, s in styles:
                st[f"value_{s}"] += 1
                if s == "plain":
                    examples[(arm, "plain")].append(f"{fn}.{k}")
            if any(s == "array" for _, s in styles):
                st["calls_with_array_value"] += 1
            if D + D in args:
                st["doubled_marker"] += 1
                examples[(arm, "doubled")].append(args[:300])
            c4.append({"arm": arm, "task": task_of(hint), "tool": fn, "text": f"<|tool_call>call:{fn}{{{args}}}<tool_call|>",
                       "styles": styles})
        for fn, aj in (r.get("calls") or []):
            try:
                a = json.loads(aj)
            except Exception:  # noqa: BLE001
                continue
            for k, v in a.items():
                if isinstance(v, str) and fn in ("edit_file", "write_file", "run_command") and "\\n" in v and "\n" not in v:
                    st["string_arg_literal_backslash_n_only"] += 1
            if any(not re.fullmatch(r"[A-Za-z_]\w*", k) for k in a):
                st["junk_key_calls"] += 1
                examples[(arm, "junk")].append(sorted(a)[:6])
            if fn == "run_skill_script":  # does the model ever fill the array-typed params?
                st["skill_script_calls"] += 1
                for k in ("args", "positional_args", "short_options"):
                    if k in a:
                        st[f"skill_script_with_{k}"] += 1
            ok, why = validate(fn, a)
            if not ok:
                st["rejected_calls"] += 1
                examples[(arm, "rejected")].append(f"{fn}: {why}")

    # scorer-equivalent: any job whose window contains a fatal is FAILED
    fatal_ts = [f["ts"] for f in fatals]
    arms = sorted({j["arm"] for j in jobs})
    summary = {"run": name, "jobs": len(jobs), "capture_records": len(recs), "scorer_fatal_events": len(fatals),
               "system_hash_to_arm": sys2arm, "arms": {}}
    for arm in arms:
        js = [j for j in jobs if j["arm"] == arm]
        st = per.get(arm, collections.Counter())
        vals = sum(st[k] for k in st if k.startswith("value_"))
        killed = [j for j in js if any(j["t_start"] <= t <= j["t_end"] for t in fatal_ts)]
        resolved = sum(bool(j.get("resolved")) for j in js)
        summary["arms"][arm] = {
            "tasks": len(js), "runner_errors": sum(1 for j in js if j.get("runner_error")),
            "resolved_local_plain_grader": resolved,
            "resolved_scorer_equivalent": sum(bool(j.get("resolved")) for j in js if j not in killed),
            "tasks_in_flight_at_fatal": len(killed),
            "mean_tool_calls": round(sum(j.get("tool_calls") or 0 for j in js) / max(1, len(js)), 2),
            "trace_rejected_calls": sum((j.get("trace") or {}).get("rejected_calls", 0) for j in js),
            "trace_tool_errors": sum((j.get("trace") or {}).get("tool_errors", 0) for j in js),
            "tasks_with_retry_storm_ge3": sum(1 for j in js if (j.get("trace") or {}).get("max_identical_failed_retry_run", 0) >= 3),
            "skill_or_subagent_calls": dict(sum((collections.Counter({k: v for k, v in (j.get("trace") or {}).get("tool_calls_by_type", {}).items()
                                                   if k in ("run_skill_script", "load_skill", "code_analyzer_agent", "get_code_subgraph")})
                                                   for j in js), collections.Counter())),
            "submitted_nonempty_patch": sum(1 for j in js if (j.get("agent_patch") or "").strip()),
            "edit_strategies": dict(sum((collections.Counter((j.get("trace") or {}).get("edit_strategies", {})) for j in js),
                                        collections.Counter())),
            "error_types_top": collections.Counter(dict(sum((collections.Counter((j.get("trace") or {}).get("error_types", {}))
                                                             for j in js), collections.Counter()))).most_common(8),
            "tasks_hitting_time_cap": sum(1 for j in js if (j.get("duration_seconds") or 0) >= 0.95 * time_cap_s),
            "tasks_hitting_call_cap": sum(1 for j in js if (j.get("tool_calls") or 0) >= call_cap),
            "mean_duration_seconds": round(sum(j.get("duration_seconds") or 0 for j in js) / max(1, len(js)), 1),
            "wire": dict(st),
            "plain_quote_rate": round(st["value_plain"] / vals, 4) if vals else None,
            "array_value_call_rate": round(st["calls_with_array_value"] / st["calls"], 4) if st["calls"] else None,
            "top_plain_params": collections.Counter(examples[(arm, "plain")]).most_common(8),
            "rejected_examples": collections.Counter(examples[(arm, "rejected")]).most_common(5),
        }
    summary["fatal_examples"] = [{"array_input": f["array_input"][:300], "hint": (f.get("session_hint") or "")[:80]}
                                 for f in fatals[:5]]
    (ev / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    with (ev / "tasks.jsonl").open("w") as f:
        for j in sorted(jobs, key=lambda j: (j["arm"], j["instance_id"])):
            f.write(json.dumps({k: j.get(k) for k in ("arm", "instance_id", "resolved", "tool_calls", "duration_seconds",
                                                      "error", "runner_error", "t_start", "t_end", "trace")}, default=str) + "\n")
    with gzip.GzipFile(ev / "c4_calls.jsonl.gz", "wb", mtime=0) as g:
        for c in c4:
            g.write((json.dumps(c, ensure_ascii=False) + "\n").encode())
    print(json.dumps({k: v for k, v in summary.items() if k != "fatal_examples"}, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
