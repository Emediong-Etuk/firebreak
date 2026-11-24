# HARNESS_NOTES — answers to the 12 Phase-0 questions

Sources, in authority order:
- **[R]** `HARNESS_README.md`, downloaded 2026-11-24, 55,940 bytes, § = README section.
- **[S]** Installed source from the official wheelhouse `metric/gemma-4-developer-agent-wheelhouse`:
  `swegemma-0.2.11`, `adk_submission-0.2.13`, `adk_eval_core-0.1.0`, `google_adk-1.36.1`, `vllm-0.19.1`.
  Hashes are in `wirelab/pinned/hashes.json`.
- **[N]** Host notebook `ryanholbrook/getting-started-gemma-4-developer-agent`.
- **[API]** Kaggle competition metadata API.

`scripts/inference.py` (the actual scorer driver) is **not** shipped in any wheel. Anything that only it decides is marked **UNKNOWN** and backed by the closest evidence available.

---

### 1. Tool signatures. Which ones take arrays?
[R §6] *"`SwegemmaContext.create_tools()` registers **9 tools**"*:

| tool | signature | array? |
|---|---|---|
| `run_command` | `(command: str)` | no |
| `submit_patch` | `()`; *"does not count toward `tool_calls`"* | no |
| `get_status` | `()`; free | no |
| `read_file` | `(filepath: str, start_line: int\|None, end_line: int\|None)` | no |
| `edit_file` | `(filepath: str, old_string: str, new_string: str, allow_multiple: bool = False)` | no |
| `write_file` | `(filepath: str, content: str)` | no |
| `get_code_neighbors` | `(node: str, edge_type: str\|None, max_neighbors: int = 50)` | no |
| `search_similar_code` | `(query: str, k: int = 10)` | no |
| `get_code_subgraph` | `(nodes: list[str])` | **YES: `nodes`** |

Skill tools come from ADK `SkillToolset` [S `google/adk/tools/skill_toolset.py`]. They are added to an agent whenever `skills:` is set:

| tool | params | array? |
|---|---|---|
| `list_skills` | none | no |
| `load_skill` | `skill_name: str` | no |
| `load_skill_resource` | `skill_name: str, file_path: str` | no |
| `run_skill_script` | `skill_name: str, file_path: str, args: object\|array<string>, short_options: object, positional_args: array<string>` | **YES: `args` (anyOf), `positional_args`** |

Verified by compiling with the official compiler: `verify/check_submission.py data/sample_submission` flags `get_code_subgraph.nodes` on both sample agents.

Related facts:
- [R §5.3] The harness coerces *"single-element list strings (`["name"]` → `"name"`) for `skill_name` and `file_path`"*. That happens **after** vLLM has parsed the call, so it can't prevent F1.
- [S `adk_eval_core/sandbox/base.py::AdkSandboxCodeExecutor`] Skill scripts run **inside Container A**: `python3 /workspace/.adk_exec_<id>.py`, with cwd set to a temp dir.
- [S] They are not `@budget_gated`, so they don't count toward `tool_calls`.

### 2. `eval_config.yaml` schema
[R §2.2] *"under an `evaluation:` key (`timeout_seconds`, `max_tool_calls`, `max_time_minutes`, `max_turns`)"*.

[R §7.1] Defaults in `inference.py`:
- 60 min per task
- 100 tool calls
- 500 turns
- 300 s per command

[N] The host notebook also reads `verification_timeout_seconds`. [R §7.1] says that value is *"fixed at `300` sec on scorer"*.

The schema has no concurrency, retry or per-tool caps.

### 3. Scoring concurrency. One vLLM server?
- **One server:** yes. [R §3.3] *"`VllmServer` on `127.0.0.1:8000`"*, a single process with `tensor_parallel_size = 4`.
- **Concurrency: UNKNOWN.** It's set inside `scripts/inference.py`, which isn't shipped. The `swegemma` default is `concurrency: int = 1` [S `swegemma/config.py:119`], and the host notebook evaluates tasks in a serial `for` loop.
- Evidence it fits anyway: public configs with an 8-minute per-task cap are scored (e.g. `bliverrigpdoge/lb-0-22-3-tweaks-40-tool-calls-no-training`, LB 0.22). Either tasks usually finish well under the cap, or the scorer runs tasks concurrently.

### 4. What happens if the vLLM server stops responding?
- The session for a task is wrapped in `asyncio.timeout(config.budget.time_minutes * 60)` [S `swegemma/harness/agent_runner.py` ~L725]. When it fires, `agent_error = "Agent exceeded session timeout"` and the fallback patch is extracted.
- `ModelRetryPlugin` [R §7.2] retries 429/5xx/`APIConnectionError`/`APITimeoutError` up to 5×. A hung server produces no error, though, just a request that never completes.
- vLLM calls `tool_parser.extract_tool_calls` **synchronously inside the async request handler** [S `vllm/entrypoints/openai/engine/serving.py::_parse_tool_calls_from_content`]. An F1 hang therefore blocks the API server's event loop for every request, and memory grows (see `evidence/wirelab/F1.md`).
- **Derived consequence:** every task after the hang burns its full `max_time_minutes` with no model output, until the host's RAM is exhausted. No server restart logic exists in `adk_submission/server.py` beyond startup health polling.
- What the scorer does at the 12 h wall is **UNKNOWN**.

### 5. `submit_patch`: mandatory? On timeout? Overwrites?
- **Not mandatory.** [R §8.1.3] *"If an agent finishes, hits `max_nudges`, exhausts `tool_calls` or `turns`, or times out **without ever calling `submit_patch()`**, the harness automatically runs `git add -N .`, `git diff --binary _swegemma_baseline`…"*, and that patch is still graded.
- [R §5.3] Once a turn ends with `patch_submitted == True`, the loop exits. So the first `submit_patch` effectively ends the task.
- [R §8.1.2] Hunks touching tests (`test_*.py`, `*_test.py`, `tests/…`) and runner config (`conftest.py`, `pytest.ini`, `pyproject.toml`, `setup.cfg`, …) are **stripped**.
- [R §10.3] Untracked scratch files in `/workspace` **are included** in the patch.

### 6. Context length and compaction: configurable?
- [R §3.1] `max_model_len = 32768`.
- [R §7.2] Compaction is configured by `inference.py`: `compaction_interval = 15`, `overlap_size = 2`, `token_threshold = 14,336`, `event_retention_size = 5`; plus a context cache.
- **Not configurable** from the submission. Only `max_output_tokens` (1–32,768) and `thinking_budget` are [R §2.4].

### 7. Allowed keys in `configs/sampling.yaml`
[R §2.4] *"All schema fields permitted: `temperature`, `top_p`, `top_k`, `max_output_tokens`, `presence_penalty`, `frequency_penalty`, `stop_sequences`, `response_mime_type`, `seed`, `thinking_config`"*.

Forbidden: `tools`, `system_instruction`, `http_options`, `safety_settings`, `response_schema`.

### 8. `agent_tool` sub-agent input schema
[R §2.3] `agent_tool: {config_path: ..., skip_summarization: true}`.

ADK `AgentTool` without an `input_schema` declares a single `request: string` parameter. That's verified by compiling the sample: the `code_analyzer_agent` tool has no array params.

### 9. Local validation/compile command and local eval CLI
- **Validate:** `adk_submission.validate_directory(dir, limits=swegemma.config.build_submission_limits()[0])` [N §6]. Our wrapper `verify/check_submission.py` also runs `compile_submission` with the real `SwegemmaContext` tools and dumps every tool schema via ADK's own LiteLLM converter.
- **Eval:** `swegemma eval --tasks tasks.jsonl --snapshots-dir snapshots --submission-dir … --results-dir … --sandbox {docker,subprocess} --concurrency N …` [R §9.1]. `--skip-agent-patch` checks baseline failure.

### 10. How a submission is made
- It's a code competition: a notebook writes `/kaggle/working/submission.zip` (agent.yaml at the root) [N §6], and the notebook version is submitted.
- CLI: `kaggle competitions submit gemma-4-developer-agent -k <user>/<kernel> -f submission.zip -v <version> -m <msg>`.
- [API] `maxDailySubmissions = 1`, `deadline = 2026-12-02T23:59Z`, `newEntrantDeadline = 2026-11-25T23:59Z`.
- The official inference notebook = [N].

### 11. vLLM version, tool parser, chat template
- [R §3.1] *"`tool_call_parser = "gemma4"`, `reasoning_parser = "gemma4"`"*.
- The wheelhouse has `vllm-0.19.1-cp38-abi3-manylinux_2_31_x86_64.whl`.
- The chat template comes from the Kaggle model files `google/gemma-4/Other/<model>/2` [N metadata]. **PENDING:** not yet downloaded.

### 12. Limits on skills
- [R §2.4] `max_skill_size_bytes` 50 MiB, `max_skills` 1,000. Extensions are limited to `.yaml .yml .md .txt .py .json .safetensors`, so **skills can only ship `.py` scripts** (no `.sh`).
- [S] Runtime: `python3` inside Container A (Python 3.13, offline, 4 GiB RAM, 2 vCPU). The timeout is `min(command_timeout_seconds, remaining session time)`.
- [S `_build_wrapper_code`] The script's cwd is a fresh temp dir holding the skill's files, and `_orig_cwd` is `/workspace`. All skill files are embedded in the wrapper for each call.
- No env restrictions beyond the container's.
