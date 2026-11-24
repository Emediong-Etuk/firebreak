"""airlock against a real git repo laid out like the harness baseline."""
import os, runpy, subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCR = ROOT / "submission/skills/airlock/scripts"


def sh(cmd, cwd):
    subprocess.run(cmd, shell=True, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    w = tmp_path / "workspace"; (w / "pkg").mkdir(parents=True); (w / "tests").mkdir()
    (w / "pkg/__init__.py").write_text(""); (w / "pkg/core.py").write_text("def f():\n    return 1\n")
    (w / "tests/test_core.py").write_text("def test_f():\n    assert True\n")
    (w / "pytest.ini").write_text("[pytest]\n"); (w / "conftest.py").write_text("# harness\n")
    sh("git init -q && git add -A && git -c user.email=x@x -c user.name=x commit -qm baseline", w)
    monkeypatch.setenv("FIREBREAK_WORKSPACE", str(w))
    return w


def run(final=False):
    return runpy.run_path(str(SCR / "check.py"), run_name="t")["run"](final=final)


def diff(w):
    sh("git add -N .", w)
    return subprocess.run(["git", "diff", "HEAD"], cwd=w, capture_output=True, text=True).stdout


def test_reverts_test_edits_and_deletes_new_tests(ws, capsys):
    (ws / "tests/test_core.py").write_text("def test_f():\n    pass  # weakened\n")
    (ws / "tests/test_new.py").write_text("x = 1\n"); (ws / "pkg/core_test.py").write_text("y = 1\n")
    (ws / "conftest.py").write_text("# changed\n"); (ws / "pytest.ini").write_text("[pytest]\naddopts=-x\n")
    (ws / "pkg/core.py").write_text("def f():\n    return 2\n")
    run()
    d = diff(ws)
    assert "pkg/core.py" in d and "tests/" not in d and "conftest" not in d and "pytest.ini" not in d and "core_test" not in d


def test_deletes_scratch_keeps_new_package_module(ws):
    for f in ("repro.py", "debug_x.py", "out.log", "notes.md.orig"):
        (ws / f).write_text("x = 1\n")
    (ws / ".firebreak").mkdir(); (ws / ".firebreak/payload.txt").write_text("p")
    (ws / "pkg/new_mod.py").write_text("def g():\n    return 3\n")
    (ws / "pkg/blob.bin").write_bytes(b"\0\1\2" * 10)
    (ws / "pkg/huge.txt").write_text("a" * (201 * 1024))
    run()
    d = diff(ws)
    assert "pkg/new_mod.py" in d
    for bad in ("repro.py", "debug_x.py", "out.log", ".firebreak", "blob.bin", "huge.txt", "notes.md.orig"):
        assert bad not in d, bad


def test_check_reports_but_final_reverts_broken(ws, capsys):
    (ws / "pkg/core.py").write_text("def f(:\n")
    (ws / "pkg/other.py").write_text("def h():\n    return 4\n")
    run(final=False)
    out = capsys.readouterr().out
    assert "does not compile" in out and "pkg/core.py" in diff(ws)
    run(final=True)
    d = diff(ws)
    assert "pkg/core.py" not in d and "pkg/other.py" in d


def test_uses_swegemma_baseline_ref(ws):
    sh("git tag _swegemma_baseline", ws)
    (ws / "pkg/core.py").write_text("def f():\n    return 5\n")
    sh("git -c user.email=x@x -c user.name=x commit -qam agentcommit", ws)
    (ws / "tests/test_core.py").write_text("bad\n")
    sh("git -c user.email=x@x -c user.name=x commit -qam agentcommit2", ws)
    run()
    d = subprocess.run(["git", "diff", "_swegemma_baseline"], cwd=ws, capture_output=True, text=True).stdout
    assert "pkg/core.py" in d and "tests/test_core.py" not in d


def test_finalize_entrypoint(ws):
    (ws / "pkg/core.py").write_text("def f(:\n")
    with pytest.raises(SystemExit) as e:
        runpy.run_path(str(SCR / "finalize.py"), run_name="__main__")
    assert e.value.code == 0 and "pkg/core.py" not in diff(ws)


def test_empty_patch_is_fine(ws, capsys):
    run()
    assert "(empty patch)" in capsys.readouterr().out
