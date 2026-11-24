# STATUS — updated 2026-11-24

**Phase:** 0 (safe submission) done, waiting on Greg. Phase 1 (Wire Lab) in progress.

## Done
- Kaggle access: the token is proxy-injected (never on disk). Greg is **already entered** in the competition (API `userHasEntered=True`).
- Data: HARNESS_README, tasks.jsonl (129 tasks), sample_submission, docker/, sandbox/setup.py, and the official harness wheels from `metric/gemma-4-developer-agent-wheelhouse`.
- `docs/HARNESS_NOTES.md`: all 12 questions answered with quotes. Unknowns are marked: scorer concurrency, and what the scorer does at 12 h.
- **F1 reproduced** on the exact pinned parser, both at the array level and end to end through `extract_tool_calls`. See `evidence/wirelab/F1.md`. Mechanism and blast radius were confirmed from the vLLM and swegemma source.
- firebreak-lite: compiled with the official compiler, **0 array params** exposed. The zip is byte-reproducible, sha256 `aedd6678…2472`.
- CPU-only submission notebook generator: `evalkit/submit_notebook/build.py`.

## Blocked / needs Greg
1. Greg's **Kaggle username** (for the notebook id `<username>/firebreak-submit`).
2. **OK to push the private CPU notebook and submit firebreak-lite** as submission #1. It uses no GPU quota.

## Next
- Phase 1: chat template, renderer, C1 fuzz, C2 gold replay (needs snapshots, ~? GB), and the F2 verdict.
