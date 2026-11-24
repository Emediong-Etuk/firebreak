"""Fast Wire Lab invariants (part of verify-lite)."""
import hashlib, json, subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
WL = ROOT / "wirelab"
sys.path.insert(0, str(WL)); sys.path.insert(0, str(WL / "pinned"))
from parse_driver import Driver  # noqa: E402
from render import render_tool_call  # noqa: E402

HASHES = json.loads((WL / "pinned" / "hashes.json").read_text())


@pytest.fixture(scope="module")
def drv():
    d = Driver(timeout_s=0.5)
    yield d
    d.close()


@pytest.mark.parametrize("name", ["gemma4_tool_parser.py", "chat_template.jinja"])
def test_pinned_hashes(name):
    f = {"gemma4_tool_parser.py": "gemma4_tool_parser.py.orig", "chat_template.jinja": "chat_template.jinja"}[name]
    assert hashlib.sha256((WL / "pinned" / f).read_bytes()).hexdigest() == HASHES[name]


def test_vendored_is_regenerated_verbatim(tmp_path):
    out = tmp_path / "v.py"
    subprocess.run([sys.executable, "-I", str(WL / "pinned/extract_pure.py"), str(WL / "pinned/gemma4_tool_parser.py.orig"),
                    str(out)], check=True, capture_output=True)
    assert out.read_text() == (WL / "pinned/gemma4_parser_pinned.py").read_text()


@pytest.mark.parametrize("tool,args", [
    ("edit_file", {"filepath": "a.py", "old_string": 'def f():\n    """Doc."""', "new_string": "x = '''y'''\n"}),
    ("run_command", {"command": 'python -m pytest "tests/t.py::test_a[case]" -q'}),
    ("write_file", {"filepath": "x.py", "content": 'd = {"a": 1, "ids": ["x[0]"]}\r\n\ttab é 😀\n'}),
    ("read_file", {"filepath": "a.py", "start_line": 10, "end_line": 20}),
])
def test_faithful_render_roundtrips(drv, tool, args):
    assert drv.classify(render_tool_call(tool, args), tool, args)["cls"] == "OK"


def test_f1_hang_on_plain_array(drv):
    t = '<|tool_call>call:get_code_subgraph{nodes:["--detail", "List[int]"]}<tool_call|>'
    assert drv.classify(t, "get_code_subgraph", {"nodes": ["--detail", "List[int]"]})["cls"] == "HANG"


def test_f2_signature(drv):
    t = ('<|tool_call>call:edit_file{old_string:<|"|>"""Doc."""<|"|><|"|>,filepath:<|"|>a.py<|"|>,'
         'new_string:<|"|>x<|"|>}<tool_call|>')
    r = drv.classify(t, "edit_file", {"filepath": "a.py", "old_string": '"""Doc."""', "new_string": "x"})
    assert r["cls"] == "REJECTED" and '<|"|>,filepath' in r["parsed_args"]


@pytest.mark.parametrize("rep", sorted((ROOT / "evidence/wirelab/reproducers").glob("*.json")), ids=lambda p: p.stem)
def test_reproducers_replay(rep):
    r = subprocess.run([sys.executable, "-I", str(WL / "replay.py"), str(rep)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
