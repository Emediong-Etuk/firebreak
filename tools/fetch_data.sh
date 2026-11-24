#!/usr/bin/env bash
# Download the small competition files + official harness wheels into data/ (git-ignored).
# Needs Kaggle API auth (see docs/DECISIONS.md D-003). Bulk snapshots/graphs/embeddings: pass --bulk.
set -euo pipefail
cd "$(dirname "$0")/.."
K="python3 tools/kaggle_cli.py"
C=gemma-4-developer-agent
get() { local f=$1 d=data/$(dirname "$1"); mkdir -p "$d"; $K competitions download $C -f "$f" -p "$d" -q >/dev/null;
        # the CLI may nest the remote path under -p; flatten it
        [ -f "$d/$f" ] && mv "$d/$f" "$d/" && rm -rf "${d:?}/${f%%/*}" || true; }
for f in HARNESS_README.md tasks.jsonl sandbox/setup.py docker/Dockerfile.public docker/Dockerfile.sandbox \
         docker/imp.py docker/telnetlib.py sample_submission/agent.yaml sample_submission/configs/sampling.yaml \
         sample_submission/eval_config.yaml sample_submission/prompts/analyzer.md sample_submission/prompts/system.md \
         sample_submission/sub_agents/code_analyzer.yaml; do get "$f"; done
mkdir -p data/wheelhouse
for w in adk_eval_core-0.1.0-py3-none-any.whl adk_submission-0.2.13-py3-none-any.whl google_adk-1.36.1-py3-none-any.whl \
         swegemma-0.2.11-py3-none-any.whl vllm-0.19.1-cp38-abi3-manylinux_2_31_x86_64.whl; do
  [ -f data/wheelhouse/$w ] || $K datasets download metric/gemma-4-developer-agent-wheelhouse -f $w -p data/wheelhouse -q >/dev/null
done
echo "data/ ready"
