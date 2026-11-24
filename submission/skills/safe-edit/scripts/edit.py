"""safe-edit: quote-safe, hash-anchored line edits for /workspace (Firebreak).

Invocation takes NO tool arguments (keeps array-typed params off the wire). The model first
writes the command (and payload) with write_file:
  .firebreak/cmd.txt      one line:  view PATH START END
                                     apply PATH START:HH END:HH
                                     insert PATH AFTER:HH          (AFTER may be 0:00 for top of file)
                                     create PATH                   (new file from the payload)
  .firebreak/payload.txt  replacement text for apply/insert, ending with a line  @@END@@
then calls run_skill_script(skill_name="safe-edit", file_path="scripts/edit.py").

Escapes (decoded in the payload only): @@TQ@@ -> three double quotes, @@SQ3@@ -> three single quotes.
They never occur in any of the 129 dev snapshots (evidence/wirelab/repo_scan.json).
HH = first 2 hex chars of md5(line without its line ending); `view` prints them.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import py_compile
import sys
import tempfile
from pathlib import Path

WS = Path(os.environ.get("FIREBREAK_WORKSPACE", "/workspace"))
FB = WS / ".firebreak"
ESC = {"@@TQ@@": '"' * 3, "@@SQ3@@": "'" * 3}
END = "@@END@@"
MAX_VIEW = 120
MAX_DIFF_LINES = 60


class EditError(Exception):
    pass


def h2(line: str) -> str:
    return hashlib.md5(line.rstrip("\r\n").encode("utf-8", "surrogateescape")).hexdigest()[:2]


def ensure_exclude() -> None:
    ex = WS / ".git" / "info" / "exclude"
    try:
        txt = ex.read_text() if ex.exists() else ""
        if ".firebreak/" not in txt.split():
            ex.parent.mkdir(parents=True, exist_ok=True)
            with ex.open("a") as f:
                f.write(("" if txt.endswith("\n") or not txt else "\n") + ".firebreak/\n")
    except OSError:
        pass


def resolve(rel: str) -> Path:
    rel = rel.strip()
    for pre in ("/workspace/", "./"):
        if rel.startswith(pre):
            rel = rel[len(pre):]
    rel = rel.lstrip("/")
    p = WS / rel
    if any(part == ".." for part in Path(rel).parts):
        raise EditError(f"path escapes workspace: {rel}")
    cur = WS
    for part in Path(rel).parts:  # refuse symlinks anywhere on the path
        cur = cur / part
        if cur.is_symlink():
            raise EditError(f"refusing symlink: {cur.relative_to(WS)}")
    real = Path(os.path.realpath(p))
    if real != Path(os.path.realpath(WS)) and Path(os.path.realpath(WS)) not in real.parents:
        raise EditError(f"path escapes workspace: {rel}")
    if ".git" in Path(rel).parts:
        raise EditError("refusing to edit inside .git")
    return p


def read_lines(p: Path) -> tuple[list[str], str, bool]:
    if not p.is_file():
        raise EditError(f"no such file: {p.relative_to(WS)}")
    raw = p.read_bytes().decode("utf-8", "surrogateescape")
    nl = "\r\n" if "\r\n" in raw else "\n"
    trailing = raw.endswith(("\n", "\r"))
    lines = raw.splitlines()
    return lines, nl, trailing


def write_lines(p: Path, lines: list[str], nl: str, trailing: bool) -> None:
    text = nl.join(lines) + (nl if (trailing and lines) else "")
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".fb_tmp_")
    with os.fdopen(fd, "wb") as f:
        f.write(text.encode("utf-8", "surrogateescape"))
    os.chmod(tmp, p.stat().st_mode & 0o7777)
    os.replace(tmp, p)


def view(p: Path, start: int, end: int) -> str:
    lines, _, _ = read_lines(p)
    start = max(1, start)
    end = min(len(lines), end, start + MAX_VIEW - 1)
    out = [f"{p.relative_to(WS)} lines {start}-{end} of {len(lines)}  (format: LINE|HH| text)"]
    for i in range(start, end + 1):
        out.append(f"{i}|{h2(lines[i - 1])}| {lines[i - 1]}")
    return "\n".join(out)


def parse_anchor(tok: str) -> tuple[int, str]:
    try:
        n, hh = tok.split(":")
        return int(n), hh.lower()
    except ValueError:
        raise EditError(f"bad anchor {tok!r}; expected LINE:HH")


def load_payload() -> list[str]:
    pf = FB / "payload.txt"
    if not pf.exists():
        raise EditError("missing .firebreak/payload.txt (write it with write_file first)")
    text = pf.read_text(encoding="utf-8", errors="surrogateescape")
    lines = text.splitlines()
    while lines and lines[-1].strip() == "":
        lines.pop()
    if not lines or lines[-1].strip() != END:
        raise EditError(f"payload must end with a line {END}")
    lines = lines[:-1]
    out = []
    for ln in lines:
        for k, v in ESC.items():
            ln = ln.replace(k, v)
        out.append(ln)
    return out


def check_anchor(lines: list[str], n: int, hh: str, p: Path) -> None:
    if n == 0 and hh in ("00", "0"):
        return
    if not (1 <= n <= len(lines)) or h2(lines[n - 1]) != hh:
        lo, hi = max(1, n - 5), min(len(lines), n + 5)
        raise EditError(f"stale anchor {n}:{hh} (file changed or wrong line). Fresh view:\n" + view(p, lo, hi))


def compile_gate(p: Path, before: bytes) -> None:
    if p.suffix != ".py":
        return
    try:
        with tempfile.TemporaryDirectory() as td:
            py_compile.compile(str(p), doraise=True, cfile=os.path.join(td, "x.pyc"))
    except py_compile.PyCompileError as e:
        p.write_bytes(before)
        lineno = getattr(e.exc_value, "lineno", None) or 1
        lines = before.decode("utf-8", "surrogateescape").splitlines()
        lo = max(1, lineno - 2)
        excerpt = "\n".join(f"{i}| {lines[i - 1]}" for i in range(lo, min(len(lines), lo + 4) + 1) if i <= len(lines))
        raise EditError(f"edit reverted: SyntaxError would be introduced: {e.exc_value}\n(original lines near {lineno}):\n{excerpt}")


def diff(p: Path, before: str, after: str) -> str:
    d = list(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{p.relative_to(WS)}",
                                  f"b/{p.relative_to(WS)}", lineterm="", n=2))
    if len(d) > MAX_DIFF_LINES:
        d = d[:MAX_DIFF_LINES] + [f"... ({len(d) - MAX_DIFF_LINES} more diff lines)"]
    return "\n".join(d)


def run(cmd: str) -> str:
    ensure_exclude()
    parts = cmd.strip().split()
    if not parts:
        raise EditError("empty .firebreak/cmd.txt")
    op = parts[0].lower()
    if op == "view" and len(parts) == 4:
        return view(resolve(parts[1]), int(parts[2]), int(parts[3]))
    if op == "create" and len(parts) == 2:
        p = resolve(parts[1])
        if p.exists():
            raise EditError(f"{p.relative_to(WS)} already exists; use apply/insert")
        payload = load_payload()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        before = b""
        write_lines(p, payload, "\n", True)
        try:
            compile_gate(p, before)
        except EditError:
            p.unlink(missing_ok=True)
            raise
        return f"OK create {p.relative_to(WS)}: {len(payload)} lines"
    if op in ("apply", "insert"):
        p = resolve(parts[1])
        lines, nl, trailing = read_lines(p)
        before_bytes = p.read_bytes()
        payload = load_payload()
        if op == "apply" and len(parts) == 4:
            (s, sh), (e, eh) = parse_anchor(parts[2]), parse_anchor(parts[3])
            if s < 1 or e < s:
                raise EditError("need 1 <= START <= END")
            check_anchor(lines, s, sh, p); check_anchor(lines, e, eh, p)
            new = lines[: s - 1] + payload + lines[e:]
            first = s
        elif op == "insert" and len(parts) == 3:
            a, ah = parse_anchor(parts[2])
            check_anchor(lines, a, ah, p)
            new = lines[:a] + payload + lines[a:]
            first = a + 1
        else:
            raise EditError(f"bad command: {cmd.strip()!r}")
        if not lines and new and not trailing:
            trailing = True
        write_lines(p, new, nl, trailing)
        compile_gate(p, before_bytes)
        after = p.read_bytes().decode("utf-8", "surrogateescape")
        last = first + len(payload) - 1
        return (f"OK {op} {p.relative_to(WS)}: new lines {first}-{max(first, last)}\n"
                + diff(p, before_bytes.decode("utf-8", "surrogateescape"), after))
    raise EditError(f"bad command: {cmd.strip()!r}. Use: view PATH START END | apply PATH S:HH E:HH | "
                    "insert PATH A:HH | create PATH")


def main(argv: list[str]) -> int:
    cmd = " ".join(argv) if argv else ""
    if not cmd:
        cf = FB / "cmd.txt"
        if not cf.exists():
            print("ERROR: write .firebreak/cmd.txt first (see SKILL.md)")
            return 1
        cmd = cf.read_text().strip().splitlines()[0] if cf.read_text().strip() else ""
    try:
        print(run(cmd))
        return 0
    except EditError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
