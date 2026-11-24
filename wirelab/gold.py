"""C2 gold replay: turn every hunk of each task's gold `patch` into the tool calls a good agent
would make, and prove the calls reproduce the gold post-image exactly.

Per file in the patch:
  * modified file -> one `edit_file` per hunk, `old_string` = smallest contiguous block that
    covers the hunk's changed lines and is UNIQUE in the file at that point (edits applied in
    order, as an agent would see the file)
  * new file      -> `write_file(filepath, content)`
  * deleted file  -> `run_command("rm -f path")`
Variants (same intent, different tool): `write_file` whole post-image, `run_command` heredoc.

Usage: python -I wirelab/gold.py  -> wirelab/corpora/c2_gold/calls.jsonl + build_report.json
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "wirelab" / "corpora" / "c2_gold"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_count: int = 0
    new_count: int = 0
    lines: list[tuple[str, str]] = field(default_factory=list)  # (tag, text) tag in ' -+'
    no_eol_old: bool = False
    no_eol_new: bool = False


@dataclass
class FilePatch:
    old: str | None
    new: str | None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new if self.new is not None else self.old


def parse_patch(text: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    cur: FilePatch | None = None
    h: Hunk | None = None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            o = ln[4:].split("\t")[0]
            n = lines[i + 1][4:].split("\t")[0]
            cur = FilePatch(None if o == "/dev/null" else o[2:], None if n == "/dev/null" else n[2:])
            files.append(cur)
            h = None
            i += 2
            continue
        m = HUNK.match(ln)
        if m and cur is not None:
            h = Hunk(int(m.group(1)), int(m.group(2) or 1), int(m.group(4) or 1))
            cur.hunks.append(h)
        elif h is not None and _open(h) and (ln[:1] in (" ", "-", "+") or ln == ""):
            # an empty line inside an open hunk is a context line whose leading space was stripped
            h.lines.append((ln[0], ln[1:]) if ln else (" ", ""))
        elif h is not None and ln.startswith("\\ No newline"):
            prev = h.lines[-1][0] if h.lines else " "
            if prev in " -":
                h.no_eol_old = True
            if prev in " +":
                h.no_eol_new = True
        i += 1
    return files


def _open(h: Hunk) -> bool:
    o = sum(1 for t, _ in h.lines if t in " -")
    n = sum(1 for t, _ in h.lines if t in " +")
    return o < h.old_count or n < h.new_count


def is_new_file(fp: FilePatch) -> bool:
    return fp.old is None or (len(fp.hunks) == 1 and fp.hunks[0].old_start == 0 and fp.hunks[0].old_count == 0)


def serialize(fps: list[FilePatch]) -> str:
    """Canonical unified diff (restores stripped blank-context prefixes and the final newline)."""
    out = []
    for fp in fps:
        out.append(f"--- {'a/' + fp.old if fp.old is not None else '/dev/null'}")
        out.append(f"+++ {'b/' + fp.new if fp.new is not None else '/dev/null'}")
        for h in fp.hunks:
            new_start = h.old_start if h.old_count else 1
            out.append(f"@@ -{h.old_start},{h.old_count} +{new_start},{h.new_count} @@")
            for tag, t in h.lines:
                out.append(tag + t)
            if h.no_eol_old or h.no_eol_new:
                out.append("\\ No newline at end of file")
    return "\n".join(out) + "\n"


def git_apply_postimage(pre_files: dict[str, bytes], patch: str) -> dict[str, bytes | None]:
    """Ground truth: apply the gold patch with `git apply` in a scratch repo."""
    with tempfile.TemporaryDirectory() as td:
        for rel, b in pre_files.items():
            p = Path(td) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)
        (Path(td) / "_p.diff").write_text(serialize(parse_patch(patch)))
        # same fallback order as the harness' first pass (plain, then --recount)
        for extra in ([], ["--recount"]):
            r = subprocess.run(["git", "apply", "--unsafe-paths", "-p1", *extra, "_p.diff"], cwd=td,
                               capture_output=True, text=True)
            if r.returncode == 0:
                break
        if r.returncode != 0:
            raise RuntimeError("git apply failed: " + r.stderr[:300])
        out = {}
        for fp in parse_patch(patch):
            p = Path(td) / fp.path
            out[fp.path] = p.read_bytes() if (fp.new is not None and p.exists()) else None
        return out


def anchor_span(content: str, hlines: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Smallest window [a, b) over hunk lines that covers every changed line and whose old side
    is non-blank and occurs exactly once in `content`. Grows upward first (agents anchor on the
    line above), then downward."""
    ch = [j for j, (tag, _) in enumerate(hlines) if tag != " "]
    a, b = ch[0], ch[-1] + 1

    def sides(a, b):
        seg = hlines[a:b]
        o = [t for tag, t in seg if tag in " -"]
        n = [t for tag, t in seg if tag in " +"]
        of, nf = "".join(x + "\n" for x in o), "".join(x + "\n" for x in n)
        # Agent-style strings omit the final newline; that is only equivalent when both sides
        # end on a non-blank line (otherwise the blank line's terminator would be lost).
        cands = []
        if o and n and o[-1].strip() and n[-1].strip():
            cands.append((of[:-1], nf[:-1]))
        cands.append((of, nf))
        cands.append((of[:-1], nf[:-1] if nf else nf))  # file end without trailing newline
        for os_, ns_ in cands:
            if os_.strip() and content.count(os_) == 1:
                return os_, ns_, True
        return of, nf, False

    while True:
        old_s, new_s, unique = sides(a, b)
        if unique:
            return old_s, new_s
        if a > 0:
            a -= 1
        elif b < len(hlines):
            b += 1
        else:
            return None


def extend_beyond_hunk(content: str, h: Hunk, delta: int) -> tuple[str, str] | None:
    """The whole hunk is ambiguous: pick the occurrence nearest the hunk's (shifted) line number,
    then add real file lines above it until the anchor is unique."""
    o = "".join(t + "\n" for tag, t in h.lines if tag in " -")
    n = "".join(t + "\n" for tag, t in h.lines if tag in " +")
    starts, i = [], content.find(o)
    while i != -1:
        starts.append(i)
        i = content.find(o, i + 1)
    if not starts:
        return None
    want_line = h.old_start - 1 + delta
    pos = min(starts, key=lambda x: abs(content.count("\n", 0, x) - want_line))
    lines_before = content[:pos].split("\n")[:-1]
    prefix = ""
    for ln in reversed(lines_before):
        prefix = ln + "\n" + prefix
        if content.count(prefix + o) == 1:
            return prefix + o, prefix + n
    return None


def build_task(task: dict) -> tuple[list[dict], dict]:
    iid = task["instance_id"]
    pre_dir = DATA / "preimages" / iid / "files"
    fps = parse_patch(task["patch"])
    pre = {}
    for fp in fps:
        if not is_new_file(fp):
            pre[fp.old] = (pre_dir / fp.old).read_bytes()
    post = git_apply_postimage(pre, task["patch"])
    calls, issues = [], []
    for fp in fps:
        target = post.get(fp.path)
        if is_new_file(fp):  # new file
            content = target.decode()
            calls.append({"tool": "write_file", "args": {"filepath": fp.path, "content": content}, "kind": "new_file"})
            continue
        if fp.new is None:  # deleted
            calls.append({"tool": "run_command", "args": {"command": f"rm -f {fp.path}"}, "kind": "delete"})
            continue
        content = pre[fp.old].decode()
        crlf = "\r\n" in content
        work = content.replace("\r\n", "\n")
        delta = 0
        for k, h in enumerate(fp.hunks):
            span = anchor_span(work, h.lines) or extend_beyond_hunk(work, h, delta)
            if span is None:
                issues.append(f"{fp.path} hunk {k}: no unique anchor within hunk")
                calls = None
                break
            old_s, new_s = span
            work = work.replace(old_s, new_s, 1)
            delta += new_s.count("\n") - old_s.count("\n")
            calls.append({"tool": "edit_file", "args": {"filepath": fp.path, "old_string": old_s, "new_string": new_s},
                          "kind": "hunk", "hunk": k})
        if calls is None:
            break
        want = target.decode().replace("\r\n", "\n")
        if work != want:
            issues.append(f"{fp.path}: replayed edits != gold post-image")
        # variants
        calls.append({"tool": "write_file", "args": {"filepath": fp.path, "content": target.decode()},
                      "kind": "variant_write_file"})
        calls.append({"tool": "run_command",
                      "args": {"command": f"cat > {fp.path} <<'FIREBREAK_EOF'\n{target.decode()}FIREBREAK_EOF"},
                      "kind": "variant_heredoc"})
        if crlf:
            issues.append(f"{fp.path}: CRLF file (edit_file normalizes \\r\\n)")
    report = {"instance_id": iid, "files": len(fps), "issues": issues, "ok": calls is not None and not
              any("!=" in i or "no unique" in i for i in issues)}
    for c in calls or []:
        c["task"] = iid
    return calls or [], report


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = [json.loads(l) for l in open(DATA / "tasks.jsonl")]
    all_calls, reports = [], []
    for t in tasks:
        if not (DATA / "preimages" / t["instance_id"] / "_scan.json").exists():
            reports.append({"instance_id": t["instance_id"], "ok": False, "issues": ["snapshot not streamed"]})
            continue
        try:
            calls, rep = build_task(t)
        except Exception as e:  # noqa: BLE001
            calls, rep = [], {"instance_id": t["instance_id"], "ok": False, "issues": [f"{type(e).__name__}: {e}"]}
        all_calls += calls
        reports.append(rep)
    with open(OUT / "calls.jsonl", "w") as f:
        for i, c in enumerate(all_calls):
            c["id"] = f"c2-{i:05d}"
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    summary = {"tasks": len(tasks), "tasks_ok": sum(r["ok"] for r in reports),
               "calls": len(all_calls), "by_kind": {}, "failures": [r for r in reports if not r["ok"]],
               "notes": sorted({i for r in reports for i in r.get("issues", []) if "CRLF" in i})}
    for c in all_calls:
        summary["by_kind"][c["kind"]] = summary["by_kind"].get(c["kind"], 0) + 1
    (OUT / "build_report.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "failures"}, indent=1))
    for r in summary["failures"][:10]:
        print("FAIL", r["instance_id"], r["issues"][:2])


if __name__ == "__main__":
    main()
