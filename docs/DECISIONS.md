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
