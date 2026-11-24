"""The guarded array parser must equal the pinned one wherever the pinned one returns,
and must return (logging a fatal) wherever the pinned one hangs."""
import gzip, json, signal, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evalkit")); sys.path.insert(0, str(ROOT / "wirelab/pinned"))
import gemma4_parser_pinned as p  # noqa: E402
from guarded_parse import make_guarded  # noqa: E402


class _T(BaseException):
    pass


def _pinned(s, t=0.2):
    def h(*_):
        raise _T()
    signal.signal(signal.SIGALRM, h)
    signal.setitimer(signal.ITIMER_REAL, t)
    try:
        return ("RET", p._parse_gemma4_array(s))
    except _T:
        return ("HANG", None)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _cases(limit=800):
    out = ['"--detail", "List[int]"', '"a]"', '<|"|>a<|"|>,<|"|>b]<|"|>', "1, 2, true", '{k:<|"|>v<|"|>}, [<|"|>x<|"|>]']
    for line in gzip.open(ROOT / "evidence/wirelab/results/c1.jsonl.gz", "rt"):
        t = json.loads(line).get("text") or ""
        i, j = t.find(":["), t.rfind("]")
        if 0 < i < j:
            out.append(t[i + 2:j])
        if len(out) >= limit:
            break
    return out


def test_guarded_matches_pinned_or_rescues_hang():
    fatals = []
    g = make_guarded(p._parse_gemma4_args, p._parse_gemma4_value, fatals.append)
    same = rescued = 0
    for c in _cases():
        kind, val = _pinned(c)
        before = len(fatals)
        got = g(c)
        if kind == "RET":
            assert got == val, c
            same += 1
        else:
            assert len(fatals) > before, c  # every pinned hang is reported
            rescued += 1
    assert same > 100 and rescued >= 2
