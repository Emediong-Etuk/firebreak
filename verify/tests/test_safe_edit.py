"""Unit + property tests for submission/skills/safe_edit/scripts/edit.py."""
import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "submission/skills/safe_edit/scripts/edit.py"


def load(ws: Path):
    os.environ["FIREBREAK_WORKSPACE"] = str(ws)
    spec = importlib.util.spec_from_file_location("fb_edit", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "workspace"
    (w / ".git" / "info").mkdir(parents=True)
    (w / ".git" / "info" / "exclude").write_text("__pycache__/\n")
    (w / "pkg").mkdir()
    return w


def h(s):
    return hashlib.md5(s.encode()).hexdigest()[:2]


def go(m, ws, cmd, payload=None):
    (ws / ".firebreak").mkdir(exist_ok=True)
    if payload is not None:
        (ws / ".firebreak" / "payload.txt").write_text(payload)
    return m.run(cmd)


def test_view_format(ws):
    (ws / "pkg/a.py").write_text("x = 1\ny = 2\n")
    m = load(ws)
    out = go(m, ws, "view pkg/a.py 1 2")
    assert f"1|{h('x = 1')}| x = 1" in out and f"2|{h('y = 2')}| y = 2" in out


def test_apply_replaces_and_decodes_triple_quotes(ws):
    (ws / "pkg/a.py").write_text('def f():\n    """Old."""\n    return 1\n')
    m = load(ws)
    out = go(m, ws, f"apply pkg/a.py 2:{h(chr(32)*4+chr(34)*3+'Old.'+chr(34)*3)} 2:{h('    ' + chr(34)*3 + 'Old.' + chr(34)*3)}",
             '    @@TQ@@New docstring.@@TQ@@\n@@END@@\n')
    assert (ws / "pkg/a.py").read_text() == 'def f():\n    """New docstring."""\n    return 1\n'
    assert out.startswith("OK apply")


def test_single_quote_triple(ws):
    (ws / "pkg/a.py").write_text("x = 1\n")
    m = load(ws)
    go(m, ws, f"apply pkg/a.py 1:{h('x = 1')} 1:{h('x = 1')}", "x = @@SQ3@@a@@SQ3@@\n@@END@@")
    assert (ws / "pkg/a.py").read_text() == "x = '''a'''\n"


def test_stale_hash_errors_with_fresh_view(ws):
    (ws / "pkg/a.py").write_text("a = 1\nb = 2\n")
    m = load(ws)
    with pytest.raises(m.EditError) as e:
        go(m, ws, "apply pkg/a.py 1:zz 1:zz", "a = 3\n@@END@@")
    assert "stale anchor" in str(e.value) and f"1|{h('a = 1')}|" in str(e.value)
    assert (ws / "pkg/a.py").read_text() == "a = 1\nb = 2\n"


def test_crlf_preserved(ws):
    (ws / "pkg/a.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    m = load(ws)
    go(m, ws, f"apply pkg/a.py 2:{h('b = 2')} 2:{h('b = 2')}", "b = 3\n@@END@@")
    assert (ws / "pkg/a.py").read_bytes() == b"a = 1\r\nb = 3\r\n"


def test_no_trailing_newline_preserved(ws):
    (ws / "pkg/a.txt").write_text("a\nb")
    m = load(ws)
    go(m, ws, f"apply pkg/a.txt 2:{h('b')} 2:{h('b')}", "c\n@@END@@")
    assert (ws / "pkg/a.txt").read_text() == "a\nc"


def test_tabs_and_unicode(ws):
    (ws / "pkg/a.py").write_text("def f():\n\treturn 'é'\n")
    m = load(ws)
    go(m, ws, f"apply pkg/a.py 2:{h(chr(9) + 'return ' + chr(39) + 'é' + chr(39))} 2:{h(chr(9) + 'return ' + chr(39) + 'é' + chr(39))}",
       "\treturn '😀→é'\n@@END@@")
    assert (ws / "pkg/a.py").read_text() == "def f():\n\treturn '😀→é'\n"


def test_insert_at_top_and_eof(ws):
    (ws / "pkg/a.py").write_text("a = 1\n")
    m = load(ws)
    go(m, ws, "insert pkg/a.py 0:00", "import os\n@@END@@")
    go(m, ws, f"insert pkg/a.py 2:{h('a = 1')}", "b = 2\n@@END@@")
    assert (ws / "pkg/a.py").read_text() == "import os\na = 1\nb = 2\n"


def test_empty_payload_deletes_lines(ws):
    (ws / "pkg/a.py").write_text("a = 1\nb = 2\nc = 3\n")
    m = load(ws)
    go(m, ws, f"apply pkg/a.py 2:{h('b = 2')} 2:{h('b = 2')}", "@@END@@\n")
    assert (ws / "pkg/a.py").read_text() == "a = 1\nc = 3\n"


def test_payload_without_end_sentinel_rejected(ws):
    (ws / "pkg/a.py").write_text("a = 1\n")
    m = load(ws)
    with pytest.raises(m.EditError, match="@@END@@"):
        go(m, ws, f"apply pkg/a.py 1:{h('a = 1')} 1:{h('a = 1')}", 'a = "2"')


def test_syntax_error_reverts(ws):
    (ws / "pkg/a.py").write_text("def f():\n    return 1\n")
    m = load(ws)
    with pytest.raises(m.EditError, match="reverted"):
        go(m, ws, f"apply pkg/a.py 2:{h('    return 1')} 2:{h('    return 1')}", "    return (1\n@@END@@")
    assert (ws / "pkg/a.py").read_text() == "def f():\n    return 1\n"


def test_non_python_not_compiled(ws):
    (ws / "pkg/a.md").write_text("x\n")
    m = load(ws)
    go(m, ws, f"apply pkg/a.md 1:{h('x')} 1:{h('x')}", "def (\n@@END@@")
    assert (ws / "pkg/a.md").read_text() == "def (\n"


@pytest.mark.parametrize("bad", ["../etc/passwd", "pkg/../../x", ".git/config", "/workspace/../x"])
def test_path_escapes_refused(ws, bad):
    m = load(ws)
    with pytest.raises(m.EditError):
        m.resolve(bad)


def test_symlink_refused(ws, tmp_path):
    (tmp_path / "outside.py").write_text("x = 1\n")
    (ws / "pkg/link.py").symlink_to(tmp_path / "outside.py")
    m = load(ws)
    with pytest.raises(m.EditError, match="symlink"):
        m.resolve("pkg/link.py")


def test_workspace_prefix_stripped(ws):
    (ws / "pkg/a.py").write_text("a = 1\n")
    m = load(ws)
    assert m.resolve("/workspace/pkg/a.py") == ws / "pkg/a.py"


def test_exclude_added_once(ws):
    (ws / "pkg/a.py").write_text("a = 1\n")
    m = load(ws)
    go(m, ws, "view pkg/a.py 1 1"); go(m, ws, "view pkg/a.py 1 1")
    assert (ws / ".git/info/exclude").read_text().count(".firebreak/") == 1


def test_firebreak_dir_not_in_git_diff(ws):
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    (ws / "pkg/a.py").write_text("a = 1\n")
    subprocess.run("git add -A && git -c user.email=x@x -c user.name=x commit -qm base", shell=True, cwd=ws, check=True)
    m = load(ws)
    go(m, ws, f"apply pkg/a.py 1:{h('a = 1')} 1:{h('a = 1')}", "a = 2\n@@END@@")
    subprocess.run(["git", "add", "-N", "."], cwd=ws, check=True)  # exactly what submit_patch runs
    d = subprocess.run(["git", "diff", "HEAD"], cwd=ws, capture_output=True, text=True).stdout
    assert "pkg/a.py" in d and ".firebreak" not in d


def test_bad_commands(ws):
    m = load(ws)
    for c in ["", "frobnicate x", "apply pkg/a.py 1:aa", "insert pkg/a.py", "apply pkg/a.py x y"]:
        with pytest.raises((m.EditError, ValueError)):
            go(m, ws, c, "@@END@@")


def test_missing_file(ws):
    m = load(ws)
    with pytest.raises(m.EditError, match="no such file"):
        go(m, ws, "view pkg/nope.py 1 2")


def test_end_before_start(ws):
    (ws / "pkg/a.py").write_text("a\nb\n")
    m = load(ws)
    with pytest.raises(m.EditError):
        go(m, ws, f"apply pkg/a.py 2:{h('b')} 1:{h('a')}", "x\n@@END@@")


def test_main_reads_cmd_file(ws, capsys):
    (ws / "pkg/a.py").write_text("a = 1\n")
    m = load(ws)
    (ws / ".firebreak").mkdir()
    (ws / ".firebreak/cmd.txt").write_text("view pkg/a.py 1 1\n")
    assert m.main([]) == 0
    assert "a = 1" in capsys.readouterr().out


def test_main_error_exit(ws, capsys):
    m = load(ws)
    assert m.main([]) == 1
    assert "cmd.txt" in capsys.readouterr().out


def test_diff_capped(ws):
    (ws / "pkg/a.txt").write_text("\n".join(f"l{i}" for i in range(200)) + "\n")
    m = load(ws)
    out = go(m, ws, f"apply pkg/a.txt 1:{h('l0')} 200:{h('l199')}", "\n".join(f"n{i}" for i in range(200)) + "\n@@END@@")
    assert "more diff lines" in out and out.count("\n") < 70


def test_huge_payload(ws):
    (ws / "pkg/a.txt").write_text("x\n")
    m = load(ws)
    big = "\n".join("y" * 100 for _ in range(20000))
    go(m, ws, f"apply pkg/a.txt 1:{h('x')} 1:{h('x')}", big + "\n@@END@@")
    assert (ws / "pkg/a.txt").read_text().count("\n") == 20000


def test_file_mode_preserved(ws):
    p = ws / "pkg/run.sh"; p.write_text("echo a\n"); p.chmod(0o755)
    m = load(ws)
    go(m, ws, f"apply pkg/run.sh 1:{h('echo a')} 1:{h('echo a')}", "echo b\n@@END@@")
    assert (p.stat().st_mode & 0o777) == 0o755


line = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n\x0b\x0c\x1c\x1d\x1e\x85  "), max_size=30)


@settings(max_examples=150, deadline=None, suppress_health_check=list(HealthCheck))
@given(before=st.lists(line, min_size=1, max_size=12), repl=st.lists(line, max_size=6), data=st.data(),
       crlf=st.booleans(), trailing=st.booleans())
def test_property_apply_equals_list_splice(tmp_path_factory, before, repl, data, crlf, trailing):
    # "a\n" and ["a", ""]-without-newline are the same text; keep inputs line-unambiguous
    assume(trailing or before[-1] != "")
    ws = tmp_path_factory.mktemp("w") / "workspace"
    (ws / ".git/info").mkdir(parents=True)
    nl = "\r\n" if crlf else "\n"
    p = ws / "f.txt"
    p.write_bytes((nl.join(before) + (nl if trailing else "")).encode())
    s = data.draw(st.integers(1, len(before)))
    e = data.draw(st.integers(s, len(before)))
    m = load(ws)
    (ws / ".firebreak").mkdir()
    enc = [r.replace('"' * 3, "@@TQ@@") for r in repl]
    (ws / ".firebreak/payload.txt").write_text("\n".join(enc + ["@@END@@"]))
    try:
        m.run(f"apply f.txt {s}:{m.h2(before[s - 1])} {e}:{m.h2(before[e - 1])}")
    except m.EditError as err:
        assert "@@" in "".join(repl) or "payload must end" in str(err)
        return
    want = before[: s - 1] + repl + before[e:]
    got = p.read_bytes().decode()
    # a file with no line break at all has no detectable newline style: the tool uses "\n"
    eff = nl if (len(before) > 1 or trailing) else "\n"
    exp = eff.join(want) + (eff if (trailing and want) else "")
    if "@@" not in "".join(repl):
        assert got == exp


def test_create_new_file_with_docstring(ws):
    m = load(ws)
    go(m, ws, "create pkg/new.py", 'def g():\n    @@TQ@@Doc.@@TQ@@\n    return 2\n@@END@@')
    assert (ws / "pkg/new.py").read_text() == 'def g():\n    """Doc."""\n    return 2\n'


def test_create_refuses_existing_and_bad_syntax(ws):
    (ws / "pkg/a.py").write_text("a = 1\n")
    m = load(ws)
    with pytest.raises(m.EditError, match="exists"):
        go(m, ws, "create pkg/a.py", "x\n@@END@@")
    with pytest.raises(m.EditError, match="reverted"):
        go(m, ws, "create pkg/b.py", "def (\n@@END@@")
    assert not (ws / "pkg/b.py").exists()
