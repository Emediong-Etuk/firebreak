PY ?= python3
HPY ?= .venv-harness/bin/python

.PHONY: zip check f1 wirelab-quick
zip:            ## deterministic submission.zip from submission/
	$(HPY) -I verify/build_zip.py submission dist/submission.zip
check: zip      ## official compile + tool-surface audit (zero arrays)
	$(HPY) -I verify/check_submission.py dist/submission.zip --out evidence/submission_audit/current.json
f1:             ## reproduce F1 on the pinned parser (CPU, ~15 s)
	$(PY) -I wirelab/repro_f1.py
