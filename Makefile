PY ?= python3
HPY ?= .venv-harness/bin/python

.PHONY: zip check f1 wirelab-quick
zip:            ## deterministic submission.zip from submission/
	$(HPY) -I verify/build_zip.py submission dist/submission.zip
check: zip      ## official compile + tool-surface audit (zero arrays)
	$(HPY) -I verify/check_submission.py dist/submission.zip --out evidence/submission_audit/current.json
f1:             ## reproduce F1 on the pinned parser (CPU, ~15 s)
	$(PY) -I wirelab/repro_f1.py

.PHONY: wirelab-test wirelab-quick wirelab-full gold report-f2
wirelab-test:   ## fast Wire Lab invariants
	$(HPY) -m pytest -q wirelab/tests
gold:           ## C2: gold patches -> minimal tool calls (needs data/preimages via tools/stream_snapshots.py)
	$(HPY) -I wirelab/gold.py
wirelab-quick:  ## C1 5k + C2 + C3 on the pinned parser
	$(HPY) -I wirelab/run_lab.py --quick
wirelab-full:   ## C1 100k + C2 + C3, then shrink, probes, F2 report, differential
	$(HPY) -I wirelab/run_lab.py --full
	$(HPY) -I wirelab/shrink.py
	$(HPY) -I wirelab/probes.py
	$(HPY) -I wirelab/report_f2.py
	$(HPY) -I wirelab/differential.py --corpora c1,c2,c3
