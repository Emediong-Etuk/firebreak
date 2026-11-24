"""Run model-output text through the pinned gemma4 parser in a sandboxed worker and classify.

Classes:
  OK        parsed into exactly the intended call (same name, same args)
  MANGLED   parsed into one call of the right tool, but the args differ from the intent
  DROPPED   no tool call extracted (vLLM returns the text as plain content)
  REJECTED  parsed, but ADK/harness validation would reject it (missing mandatory param, unknown tool)
  HANG      parser did not return within the watchdog (vLLM API server event loop would block)
  CRASH     worker died (e.g. MemoryError under RLIMIT_AS) or the parser raised out of extract

Each case runs in a long-lived forked worker with RLIMIT_AS and a per-case SIGALRM watchdog;
a hung/killed worker is respawned, so 100k cases stay fast.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import resource
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pinned"))
sys.path.insert(0, str(HERE))

CLASSES = ("OK", "MANGLED", "DROPPED", "REJECTED", "HANG", "CRASH")


class _Timeout(BaseException):  # must escape the parser's own `except Exception`
    pass


def _alarm(signum, frame):
    raise _Timeout()


def _worker(conn, timeout_s: float, mem_bytes: int) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    signal.signal(signal.SIGALRM, _alarm)
    import gemma4_parser_pinned as p
    while True:
        try:
            text = conn.recv()
        except EOFError:
            return
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
        try:
            res = ("RET", p.extract_tool_calls(text))
        except _Timeout:
            res = ("HANG", None)
        except MemoryError:
            res = ("CRASH", "MemoryError")
        except Exception as e:  # noqa: BLE001
            res = ("CRASH", f"{type(e).__name__}: {e}")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            conn.send(res)
        except Exception:  # noqa: BLE001  (e.g. result unpicklable / too large)
            conn.send(("CRASH", "send failed"))
        if res[0] in ("HANG", "CRASH"):
            return  # heap may be huge; let the parent respawn a clean worker


@dataclass
class Driver:
    timeout_s: float = 2.0
    mem_bytes: int = 1 << 30
    _proc: mp.Process | None = field(default=None, init=False)
    _conn: object = field(default=None, init=False)

    def _spawn(self):
        ctx = mp.get_context("fork")
        a, b = ctx.Pipe()
        self._proc = ctx.Process(target=_worker, args=(b, self.timeout_s, self.mem_bytes), daemon=True)
        self._proc.start()
        self._conn = a

    def raw(self, text: str):
        if self._proc is None or not self._proc.is_alive():
            self._spawn()
        self._conn.send(text)
        # parent-side hard deadline in case SIGALRM can't interrupt (e.g. stuck in C code)
        if self._conn.poll(self.timeout_s + 3):
            try:
                out = self._conn.recv()
            except EOFError:
                out = ("CRASH", "worker died")
        else:
            out = ("HANG", "parent deadline")
        if out[0] in ("HANG", "CRASH"):
            if self._proc.is_alive():
                self._proc.kill()
            self._proc.join()
            self._proc = None
        return out

    def classify(self, text: str, intent_name: str, intent_args: dict) -> dict:
        from validate import validate
        kind, payload = self.raw(text)
        if kind != "RET":
            return {"cls": kind, "detail": payload}
        tools_called, calls, _content = payload
        if not tools_called or not calls:
            return {"cls": "DROPPED", "detail": "no tool call extracted"}
        name, args_json = calls[0]
        args = json.loads(args_json)
        rec = {"parsed_name": name, "parsed_args": args, "n_calls": len(calls)}
        ok, why = validate(name, args)
        if not ok:
            return {"cls": "REJECTED", "detail": why, **rec}
        if len(calls) != 1 or name != intent_name or args != intent_args:
            return {"cls": "MANGLED", "detail": _diff(intent_args, args), **rec}
        return {"cls": "OK", "detail": "", **rec}

    def close(self):
        if self._proc is not None and self._proc.is_alive():
            self._proc.kill()
            self._proc.join()


def _diff(a: dict, b: dict) -> str:
    keys = sorted(set(a) | set(b))
    bad = [k for k in keys if a.get(k, "<absent>") != b.get(k, "<absent>")]
    return "differs: " + ",".join(map(str, bad))[:200]
