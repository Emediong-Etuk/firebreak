"""airlock finalize: check.py + revert any changed .py that still fails to compile. Zero arguments."""
import os
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
check = runpy.run_path(str(Path(__file__).resolve().parent / "check.py"), run_name="airlock_check")
sys.exit(check["run"](final=True))
