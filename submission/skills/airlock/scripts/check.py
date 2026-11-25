"""airlock: make the patch safe to hand to a developer, right before submit_patch (Firebreak).

Zero tool arguments. Two entry points:
  scripts/check.py      clean + report (never reverts library code)
  scripts/finalize.py   same, then reverts any changed .py that still fails to compile
                        so the rest of the patch still applies

What it does inside /workspace (diffs against _swegemma_baseline if present, else HEAD, exactly like
submit_patch):
  * reverts edits to existing test files and runner config; deletes NEW untracked test files
    (the grader applies its own test_patch; a developer must never receive weakened tests)
  * deletes scratch: .firebreak/, root-level repro*/debug*/scratch*/*.log/*.out, binaries, files > 200 KB
  * keeps new .py files inside existing package directories (feature work may add modules)
  * py_compile every changed .py; prints `git diff --stat` and every removal with its reason
"""
from __future__ import annotations

import fnmatch
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path



def _workspace() -> Path:
    """The repo the agent works in. ADK runs skill scripts from a temp copy of the skill (cwd != repo), so
    look for it: the scorer's Docker /workspace, else the sandbox shell's PWD / PYTHONPATH (local runs)."""
    cands = [os.environ.get("FIREBREAK_WORKSPACE"), "/workspace", os.environ.get("PWD"),
             *(os.environ.get("PYTHONPATH") or "").split(os.pathsep)]
    for c in cands:
        if c and (Path(c) / ".git").exists():
            return Path(c)
    return Path(os.environ.get("FIREBREAK_WORKSPACE") or "/workspace")


WS = _workspace()
MAX_BYTES = 200 * 1024
TEST_GLOBS = ("test_*.py", "*_test.py", "conftest.py")
TEST_DIRS = {"tests", "test", "testing"}
CONFIG = {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", ".pytest.ini", "sitecustomize.py", "usercustomize.py"}
SCRATCH_ROOT = ("repro*", "debug*", "scratch*", "tmp_*", "*.log", "*.out", "*.orig", "*.rej", "*.bak")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=WS, capture_output=True, text=True).stdout


def base() -> str:
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", "_swegemma_baseline"], cwd=WS, capture_output=True, text=True)
    return "_swegemma_baseline" if r.returncode == 0 else "HEAD"


def is_test(rel: str) -> bool:
    parts = Path(rel).parts
    name = parts[-1]
    if any(fnmatch.fnmatch(name, g) for g in TEST_GLOBS):
        return True
    return rel.endswith(".py") and any(p in TEST_DIRS for p in parts[:-1])


def is_config(rel: str) -> bool:
    return Path(rel).name in CONFIG and len(Path(rel).parts) == 1 or Path(rel).name.endswith(".pth")


def is_binary(p: Path) -> bool:
    try:
        return b"\0" in p.read_bytes()[:8192]
    except OSError:
        return False


def inside_existing_package(rel: str, tracked_dirs: set[str]) -> bool:
    parent = str(Path(rel).parent)
    return parent != "." and parent in tracked_dirs


def compiles(p: Path) -> str | None:
    try:
        with tempfile.TemporaryDirectory() as td:
            py_compile.compile(str(p), doraise=True, cfile=os.path.join(td, "x.pyc"))
        return None
    except py_compile.PyCompileError as e:
        return str(e.exc_value)


def run(final: bool) -> int:
    b = base()
    removed: list[str] = []
    changed = [l for l in git("diff", "--name-only", b).splitlines() if l]
    untracked = [l for l in git("ls-files", "--others", "--exclude-standard").splitlines() if l]
    tracked_dirs = {str(Path(f).parent) for f in git("ls-files").splitlines()}

    # 1) tests + runner config: revert tracked edits, delete new ones
    for rel in changed:
        if is_test(rel) or is_config(rel):
            subprocess.run(["git", "checkout", b, "--", rel], cwd=WS, capture_output=True)
            removed.append(f"reverted {rel}: test/runner-config file (grader applies its own tests)")
    for rel in list(untracked):
        p = WS / rel
        if is_test(rel):
            p.unlink(missing_ok=True); untracked.remove(rel)
            removed.append(f"deleted {rel}: new test file")

    # 2) scratch, binaries, oversized files (untracked only; tracked files are the user's code)
    fb = WS / ".firebreak"
    if fb.exists():
        shutil.rmtree(fb, ignore_errors=True)
        removed.append("deleted .firebreak/: Firebreak scratch")
    for rel in list(untracked):
        p = WS / rel
        if not p.exists() or rel.startswith(".firebreak/"):
            continue
        root_level = len(Path(rel).parts) == 1
        why = None
        if root_level and any(fnmatch.fnmatch(Path(rel).name, g) for g in SCRATCH_ROOT):
            why = "root-level scratch file"
        elif p.is_file() and p.stat().st_size > MAX_BYTES:
            why = f"file over {MAX_BYTES // 1024} KB"
        elif p.is_file() and is_binary(p):
            why = "binary file"
        elif rel.endswith(".py") and root_level:
            why = "new root-level .py (scratch script, not part of a package)"
        elif rel.endswith(".py") and not inside_existing_package(rel, tracked_dirs):
            why = None  # new package dirs are allowed (feature work); reported in stat below
        if why:
            p.unlink(missing_ok=True)
            removed.append(f"deleted {rel}: {why}")

    # 3) compile gate on every changed / new .py
    py = sorted({r for r in changed if r.endswith(".py") and (WS / r).exists()} |
                {r for r in untracked if r.endswith(".py") and (WS / r).exists()})
    broken = []
    for rel in py:
        err = compiles(WS / rel)
        if err:
            broken.append((rel, err))
    if final:
        for rel, err in broken:
            if rel in changed:
                subprocess.run(["git", "checkout", b, "--", rel], cwd=WS, capture_output=True)
            else:
                (WS / rel).unlink(missing_ok=True)
            removed.append(f"reverted {rel}: still fails to compile ({err.splitlines()[0][:120]})")

    subprocess.run(["git", "add", "-N", "."], cwd=WS, capture_output=True)
    stat = git("diff", "--stat", b).strip() or "(empty patch)"
    print("AIRLOCK", "FINAL" if final else "CHECK", f"(base {b})")
    print(stat)
    for r in removed:
        print(" -", r)
    if broken and not final:
        for rel, err in broken:
            print(f" ! {rel} does not compile: {err.splitlines()[0][:160]}")
        print("Fix the files above, then run airlock finalize.py before submit_patch.")
    if not removed and not broken:
        print(" - nothing to clean")
    return 0


if __name__ == "__main__":
    sys.exit(run(final=False))
