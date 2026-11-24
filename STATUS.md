# STATUS — updated 2026-11-24

**Phase:** 0 (safe submission) done, waiting on Greg. Phase 1 (Wire Lab) in progress.

## Done
- Kaggle access: the token is proxy-injected (never on disk). Greg is **already entered** in the competition (API `userHasEntered=True`).
- Data: HARNESS_README, tasks.jsonl (129 tasks), sample_submission, docker/, sandbox/setup.py, and the official harness wheels from `metric/gemma-4-developer-agent-wheelhouse`.
- `docs/HARNESS_NOTES.md`: all 12 questions answered with quotes. Unknowns are marked: scorer concurrency, and what the scorer does at 12 h.
- **F1 reproduced** on the exact pinned parser, both at the array level and end to end through `extract_tool_calls`. See `evidence/wirelab/F1.md`. Mechanism and blast radius were confirmed from the vLLM and swegemma source.
- firebreak-lite: compiled with the official compiler, **0 array params** exposed. The zip is byte-reproducible, sha256 `aedd6678…2472`.
- CPU-only submission notebook generator: `evalkit/submit_notebook/build.py`.

## Submissions
- **#1 firebreak-lite**: ref `58832270`, submitted 2026-11-24 13:32 UTC from private CPU notebook `emediongetuk/firebreak-submit` v1. Status PENDING (expect a score in 20–29 h). Tag `sub-1-b51cc4d6`.

## Blocked / needs Greg
- Nothing right now. The next submission slot opens 2026-11-25.

## Next
- Phase 1: chat template, renderer, C1 fuzz, C2 gold replay (needs snapshots, ~? GB), and the F2 verdict.
