"""Deterministic submission.zip: sorted entries, fixed timestamp and permissions, so a clean
checkout always reproduces the same sha256. Usage: build_zip.py SRC_DIR OUT_ZIP"""
import hashlib, sys, zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
files = sorted(p for p in src.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for p in files:
        if p.is_symlink():
            sys.exit(f"refusing symlink {p}")
        info = zipfile.ZipInfo(p.relative_to(src).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, p.read_bytes(), compresslevel=9)
print(hashlib.sha256(out.read_bytes()).hexdigest(), out)
