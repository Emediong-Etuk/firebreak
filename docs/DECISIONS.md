# Decisions log

Each entry records the date, the decision, the evidence behind it, and what would make us revisit it.

**D-001 (2026-11-24): The harness wheels come from the host dataset, not the competition data.**
The brief says "the wheels for `swegemma`, `adk-submission`, `adk-eval-core` and `vllm`" are in the competition wheelhouse. They are not: `wheels/` holds only the task repos' dependencies (124 files). The harness wheels live in the Kaggle dataset `metric/gemma-4-developer-agent-wheelhouse`, which is attached by the host's Getting Started notebook. None of them are on PyPI except vllm. We use the host dataset as the source of truth.

**D-002 (2026-11-24): `verify-lite` may use the PyPI vLLM wheel.**
The wheels differ in bytes: competition sha256 `73c69baf…`, PyPI sha256 `71a87f46…`. The gemma4 parser files inside them are byte-identical: `gemma4_tool_parser.py` sha256 `682b2152…` and `gemma4_utils.py` `be0bd960…`. So `verify-lite` downloads the PyPI wheel and checks the **parser file** hash, not the wheel hash.

**D-003 (2026-11-24): The Kaggle token is proxy-injected, never written to disk.**
This replaces the brief's `~/.kaggle/kaggle.json` plan. The KGAT token is stored as a network secret on the `firebreak` cloud environment and injected into requests to `api.kaggle.com`. `tools/kaggle_cli.py` sets a placeholder `KAGGLE_API_TOKEN` and bypasses only the CLI's start-up token introspection, which sends the token in a request body. Every real API call still authenticates through the header.

**D-004 (2026-11-24): Per-task budget is 6 min / 40 tool calls / 180 s per command / 80 turns.**
- Scorer concurrency is UNKNOWN (HARNESS_NOTES Q3). `swegemma` defaults to concurrency 1, and the brief's "~6 min per task" implies a serial run.
- Worst case if serial and *every* task hits the cap: 120 × (6 + ~0.7 setup) ≈ 13.4 h. That exceeds 12 h, so the brief's "≥ 45 min margin" can't be proven for that pathological case.
- Expected case: public configs with **8 min** caps (LB 0.22) are scored, so the real per-task mean is well under the cap. 6 min is strictly more conservative than a configuration known to score.
- 40 tool calls also comes from the LB 0.22 config.
- Revisit when Phase 3 measures per-task wall time, or when hosts state the concurrency.

**D-005 (2026-11-24): firebreak-lite drops the sample's sub-agent and its dummy LoRA adapters.**
- The host notebook calls `adapters/main_lora` and `tool_lora` "dummy LoRA adapters" (r=4, layer 0 only). Enabling LoRA in vLLM costs throughput for no trained benefit.
- The `code_analyzer_agent` sub-agent adds LLM turns. Per operating rule 7 it returns only if Phase 3 shows it pays.

**D-006 (2026-11-24): Skills add arrays to the tool surface.**
- ADK `run_skill_script` declares `args: anyOf[object, array<string>]` and `positional_args: array<string>`.
- Adding *any* skill therefore exposes array-typed parameters to the model, which is exactly the F1 surface: one plain-quoted `]` in an array kills the scorer's server.
- `firebreak-lite` ships **no skills**. Whether full Firebreak ships `safe_edit`/`airlock` as skills is decided by Wire Lab C3/C4 data on how often Gemma emits array args when they're optional. The alternative is to deliver the same behaviour through scalar-only harness tools.

**D-007 (2026-11-24): No array params is necessary but not sufficient (F1b).**
- Wire Lab shows every hang needs an array-valued argument *on the wire*. That's 1,274 of 1,279 C1 hangs with a declared array, and 0 of 52,329 faithful scalar-only calls.
- But a model that **plain-quotes** a scalar string can still put an array on the wire. Realistic code reproduces it: a plain-quoted `run_command` containing `m = {"k": 1, "types": ["List[int]"]}` hangs the pinned parser (`evidence/wirelab/realistic_probes.json`). So does a string arg wrapped as `["test_x[case]"]`.
- Consequences:
  - D1 stays: never declare arrays.
  - Prompt rules can't stop emission drift; only the model's training does. How often Gemma 4 31B plain-quotes is the decisive number, and it comes from C4 (captured completions, Phase 3).
  - Skills stay out until C4 shows `run_skill_script` calls are emitted marker-quoted with no `args`/`positional_args`.

**D-008 (2026-11-24): F2 is a model-side emission fault, not a parser bug.**
- 1,153/1,153 faithful renders of all 129 gold fixes parse back exactly.
- The reported signature (junk key `<|"|>,filepath`, *"mandatory input parameters are not present: filepath"*) reproduces only when the model emits a stray `<|"|>` next to a content quote. The shrunk minimal trigger is content `"`.
- Exposure: 38 of 129 tasks need a triple-quoted argument, and 24 have an argument that ends in a quote.
- The safe_edit escape alphabet targets exactly those quote runs. The scan of 195,270 files in all 129 snapshots found 0 `@@…@@` tokens, so the alphabet is collision-free.

**D-009 (2026-11-24): Capture runs pin the host's cp312 image and use fp16 on T4.**
- The wheelhouse holds cp312 wheels. Kaggle's default image is Python 3.13, so the install failed (v1). The fix is pinning `gcr.io/kaggle-private-byod/python@sha256:37c64f7d…`, the image used by the host's notebook.
- On T4 (compute capability 7.5), `dtype='auto'` resolves to the model's bf16, which vLLM refuses (v2). `torch.cuda.is_bf16_supported()` also returns True on T4 because it counts emulation (v3). The fix is to choose fp16 whenever compute capability is below 8.0.
- fp16 numerical behaviour of Gemma 4 E4B is an open risk. The smoke gate aborts before any task if output is garbage.
- E4B-on-T4 numbers are **fault-shape data**. They are not the scorer's 31B/L4/bf16 rates.

**D-010 (2026-11-24): Gemma 4 can't be served on T4 with vLLM 0.19.1. Capture runs need L4.**
- v4 got past dtype: Marlin W4A16 loaded on T4.
- It then failed in memory profiling. Gemma 4 has `global_head_dim=512`, so vLLM forces `TRITON_ATTN`, and `kernel_unified_attention_2d` needs 98,304 B of shared memory against T4's 65,536 B limit.
- There's no config knob for this short of patching vLLM. The evidence is in `evidence/runs/2026-11-24_e4b_t4x2_attempts/attempts.json`.
- C4 data must come from L4×4 (compute capability 8.9, bf16), which is also the scorer's hardware.

**D-011 (2026-11-25): Capture runs fail fast and have a hard wall clock.**
- Run v5 (31B on L4×4) spent about 12 GPU hours and produced no task data (`evidence/runs/2026-11-24_31b_l4x4_v5/failure.json`).
- What happened:
  - vLLM's sampler warm-up ran 256 dummy requests (the `max_num_seqs` default) and ran out of CUDA memory at 0.90 GPU memory utilisation.
  - The engine died but the API process stayed up. `VllmServer.start()` only gave up at its 25 min `startup_timeout`.
  - The notebook then never finished, until Kaggle's 43,200 s cell limit. The exact hang is unproven; orphaned vLLM workers are the likely cause.
- Fixes in `evalkit/run_slice.py` and `evalkit/notebook/build_capture.py`:
  - `--max-num-seqs 16`. The run needs at most about 8 concurrent sequences.
  - A log watcher kills the server's whole process tree on a fatal engine error, so start-up fails in seconds.
  - Every exit path kills all descendants and calls `os._exit`. Each job runs in its own process group and is `killpg`'d.
  - A whole-run `--deadline-minutes` skips jobs not started in time.
  - The notebook cell runs the orchestrator in its own session, with output to a file and a hard limit of deadline + 40 min, then kills the group.
- Tested locally: a fake server that logs the v5 OOM is killed with its child in about 20 s, and an early error exits in 2 s.
- The scorer's own vLLM settings are the host's, not ours, so this affects only our capture runs.

**D-012 (2026-11-25): Submission #2 stays skill-free. C4 says arrays are rare but doesn't measure skills.**
- Evidence (v6, 31B on L4×4, 570 calls):
  - 0 array values on the wire. 1 plain-quoted value in about 950 string values (0.1%).
  - The array-typed `get_code_subgraph` was available to the sample arm and never called.
- What it does not measure: neither arm exposed skills, so `run_skill_script` (`args`/`positional_args` arrays) emission is unmeasured.
- An F1 hang kills the scorer's server for every remaining task. A 0.1% per-value drift rate across about 120 tasks × 20+ calls is not negligible against that blast radius.
- So skills ship only after a capture arm with `submission/` (skills) shows its `run_skill_script` calls are array-free and marker-quoted.
- Cheap evidence-backed fixes for the skill-free config:
  - Tell the model `write_file` is workspace-only. Scratch scripts go through `run_command` (heredoc to /tmp or `python - <<EOF`). This cost a call in 6/12 tasks.
  - Revisit the time cap: 7/12 tasks hit 5 min.
- Revisit when a skills arm is captured.

**D-013 (2026-11-25): Skills don't ship. Submission #2 is skill-free firebreak-lite plus the fixes C4 points to.**
- v7 settles the wire question: 31B left the `run_skill_script` arrays empty in all 10 calls.
- But the skills didn't earn their place:
  - Used in 3/12 tasks.
  - The safe-edit protocol costs about 4 calls per edit.
  - The skills arm resolved 2/12 against firebreak-lite's 3/12, submitted fewer patches (7 vs 9) and hit the time cap more (10 vs 7).
  - n=12 proves nothing about the difference, and there is no sign of a benefit.
- Per operating rule 7 (add only what pays), skills stay out.
- Submission #2 = firebreak-lite plus prompt fixes for the two faults C4 found in every arm:
  1. `write_file` is workspace-only: 11 wasted calls across v6/v7.
  2. When `edit_file` is rejected for a missing parameter, don't resend the same call. Rewrite with a smaller `old_string` that avoids backticks and quotes at the ends, or rewrite the file with `write_file`. This stops the F2 retry storm.
- Revisit skills only with a protocol that costs at most 1 call per edit.

**D-014 (2026-11-25): Prompt rules can't fix F2; the `/tmp` rule stays.**
- In v8, firebreak-v2's `/tmp` rule removed every path-traversal error (6/12 tasks before, 0 after).
- Its F2 retry rule had no effect: 10 identical rejected calls in a row on fastapi_14605, the same task that stormed 8× in v7.
- F2 is a model-side delimiter slip (D-008) the model can't see. The rejection message names the missing parameter, not the cause, so it keeps re-emitting the same text.
- All 4 configs ran that task. Two hit the retry storm (8 and 10 rejections), the sample arm had 1 rejection, and lite had none. The cost is the rest of one task's budget, not the run.
- The rule is kept: it is harmless and documents the fault. A real fix would have to stop the call being re-emitted unchanged, which the competition's fixed tool surface doesn't allow.
