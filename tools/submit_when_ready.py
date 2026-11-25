"""Submit a pinned code-submission kernel version once no submission is pending.

Kaggle allows one pending submission per team ("Your team already has 1 pending submissions"), so an
approved submission may have to wait for the previous one to finish scoring.

Usage: python3 tools/submit_when_ready.py KERNEL VERSION EXPECT_SHA256 MESSAGE
Exit codes: 0 submitted (prints the new ref), 3 still blocked by a pending submission, 1 error.
Before submitting it re-downloads the kernel version's submission.zip and checks its sha256.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMP = "gemma-4-developer-agent"


def cli(*args: str) -> str:
    r = subprocess.run([sys.executable, str(ROOT / "tools/kaggle_cli.py"), *args], capture_output=True, text=True)
    return r.stdout + r.stderr


def main() -> int:
    kernel, version, expect, msg = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    subs = cli("competitions", "submissions", COMP)
    rows = [l for l in subs.splitlines()[2:] if l.strip()]
    pending = [l for l in rows if "PENDING" in l]
    for l in rows[:3]:
        print("[subs]", l[:160])
    if pending:
        print(f"BLOCKED: {len(pending)} pending submission(s)")
        return 3
    with tempfile.TemporaryDirectory() as td:
        cli("kernels", "output", kernel, "-p", td)
        z = Path(td, "submission.zip")
        if not z.exists():
            print("ERROR: kernel output has no submission.zip")
            return 1
        got = hashlib.sha256(z.read_bytes()).hexdigest()
    if got != expect:
        print(f"ERROR: kernel output sha256 {got} != expected {expect}")
        return 1
    out = cli("competitions", "submit", COMP, "-k", kernel, "-f", "submission.zip", "-v", version, "-m", msg)
    print(out.strip()[-400:])
    after = cli("competitions", "submissions", COMP).splitlines()[2:3]
    if after and msg[:30] in after[0]:
        print("SUBMITTED:", after[0][:160])
        return 0
    print("ERROR: submission not visible after submit")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
