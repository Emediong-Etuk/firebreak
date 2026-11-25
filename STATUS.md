# STATUS — updated 2026-11-24

**Phase:** 1 (Wire Lab) nearly done. Submission #1 is pending scoring.

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

## Phase 1 results (all in `evidence/wirelab/`)
- **F1: reproduced** (`F1.md`). **F1b**: a realistic hang with zero array params, if the model plain-quotes code (`realistic_probes.json`, DECISIONS D-007).
- **F2: not reproduced from faithful renders. Reproduced under model drift**, with the exact reported signature (`F2.md`, D-008).
- C2 gold replay: all 129 gold patches rebuilt as minimal-anchor tool calls (1,153 calls), which replay byte-exact and parse 100% OK on the pinned wire.
- C1 fuzz: 100k seeded cases. C3: 12 deviation operators × gold calls = 10,292 cases. Shrunk reproducers are in `reproducers/`.
- Repo scan: 0 wire tokens and 0 `@@…@@` tokens across 195,270 files.
- Vendored vs real parser differential: **111,445/111,445 identical** (C1+C2+C3, hang behaviour included).

## Phase 2 (built)
- `safe-edit` skill: expressibility **129/129** host-side (1,987 commands). C5: **4,669/4,669 OK** on the wire.
- `airlock` skill. 37 unit/property tests.
- `make verify-lite` passes from a fresh venv in about 1.5 min (no Kaggle account).
- Firebreak v1 candidate in `submission/` uses skills. The audit flags `run_skill_script` arrays, so shipping it is gated on C4.

## Capture runs (C4)
- v1–v4 (E4B, T4×2): failed at start-up. T4 can't serve Gemma 4 (D-010).
- v5 (31B, L4×4, 40 tasks × 2 arms): **no task ran.** vLLM ran out of memory in its sampler warm-up at 19:07 UTC, its API process stayed up, start-up timed out at about 19:26, and the notebook then hung until Kaggle's 12 h cell limit (07:01 UTC). About 12 GPU hours were spent. Evidence: `evidence/runs/2026-11-24_31b_l4x4_v5/failure.json`. Fixes: D-011.
- **v6 (31B, L4×4, 12 fastapi tasks × 2 arms): complete in 39 min, about 0.65 GPU h.** Evidence: `evidence/runs/2026-11-25_31b_l4x4_v6/`.
  - Wire: 570 calls, **0 array values**, 0 SCORER_FATAL. 1 plain-quoted value out of about 950 top-level string values; the other 704 were marker-quoted.
  - The sample arm never called array-typed `get_code_subgraph` or its sub-agent.
  - New fault, **JSON drift**: 5 calls had a JSON-style key (`start_line": 151` → key `start_line"`), which ADK drops silently. One call echoed a `budget_warning` JSON fragment into `edit_file`.
  - Literal `\n` escapes appeared in 33 edit/write string args. swegemma's `unescaped` edit strategy repaired 15 of 64 successful edits, so the cost was zero.
  - firebreak-lite wasted a call in 6/12 tasks on `write_file /tmp/repro.py` (path-traversal error).
  - Time is the binding cap: 7/12 tasks per arm hit the 5 min limit; 2/12 hit 40 calls.
  - Resolved (local plain grader, n=12, not significant): sample 2, firebreak-lite 3.
- **v7 (31B, L4×4, skills arm `submission/` on the same 12 tasks): complete in 25 min, about 0.45 GPU h.** Evidence: `evidence/runs/2026-11-25_31b_l4x4_v7_skills/`.
  - Wire-safe: 10 `run_skill_script` calls, **0 with `args`/`positional_args`**, 0 array values, 0 SCORER_FATAL.
  - Rarely used: skills appeared in 3/12 tasks (8 safe-edit calls in one task, 2 airlock finalizes). The model mostly used `edit_file`.
  - **F2 seen live**: content containing backticks led the model to close `new_string` with a backtick instead of `<|"|>`. That swallowed `old_string`, and 8 calls were rejected in fastapi_14605.
  - No gain: resolved 2/12 (sample 2, lite 3); non-empty patch 7/12 (lite 9); 10/12 hit the 5 min cap (lite 7).

## Next
- Phase 3 (needs a GPU OK): capture real completions (C4). This decides how often Gemma plain-quotes.
