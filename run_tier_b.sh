#!/usr/bin/env bash
# ==============================================================================
# InCIS 2027 - Tier B runner: live vLLM validation with INCREMENTAL PUSHES
# ==============================================================================
# Every stage lands on GitHub as it completes:
#   1. vLLM startup logs  -> pushed right after launch (pass OR fail)
#   2. live benchmark CSV -> pushed when the run finishes
#   3. live robustness CSV-> pushed when the run finishes
# Failed stages push their logs too, so failures are diagnosable remotely.
#
# Usage (no sudo for anything - all packages via pip inside .venv):
#   SKIP_TIER_B=0 HF_TOKEN=... GIT_PUSH_TOKEN=... nohup bash run_tier_b.sh > tierb_console.log 2>&1 &
# ==============================================================================
set -uo pipefail

STAMP=$(date +%Y%m%d_%H%M%S)
MASTER_LOG="tierb_${STAMP}.log"
exec > >(tee -a "${MASTER_LOG}") 2>&1

echo "=================================================================="
echo " Tier B live validation - started $(date)"
echo "=================================================================="

export PATH="$HOME/.local/bin:$PATH"
: "${HF_TOKEN:?export HF_TOKEN=hf_... (gated model access). Aborting.}"
export HF_TOKEN

# --- env + deps (pip only, no sudo anywhere) ---------------------------------
source .venv/bin/activate 2>/dev/null || { echo "[ERROR] .venv missing - run run_all_dgx.sh once first."; exit 1; }
python -c "import vllm" 2>/dev/null || pip install -q vllm
python -c "import ninja" 2>/dev/null || pip install -q ninja
python - <<'PY' || { echo "[ERROR] HF_TOKEN invalid."; exit 1; }
from huggingface_hub import whoami
print(f"[HF] authenticated as: {whoami().get('name')}")
PY

# --- git identity + push function (rebase-safe, incremental) ------------------
git config user.email "aadisharma2808@gmail.com" 2>/dev/null || true
git config user.name "Aadi Sharma (DGX)" 2>/dev/null || true

if [ -n "${GIT_PUSH_TOKEN:-}" ]; then
    PUSH_URL="https://x-access-token:${GIT_PUSH_TOKEN}@github.com/Aadisharma1/Swarm-coord-in-ddil.git"
    GIT_PUSH_OK=1
elif command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh auth setup-git >/dev/null 2>&1 || true
    PUSH_URL="origin"
    GIT_PUSH_OK=1
else
    PUSH_URL="origin"; GIT_PUSH_OK=0
    echo "[GIT][WARN] no GIT_PUSH_TOKEN and no gh login - results stay local."
fi

push_stage() {
    echo "[GIT] ---- pushing stage snapshot ($(date +%H:%M:%S)) ----"
    git pull --rebase >/dev/null 2>&1 || true
    git add vllm_node_gpu_*.log tierb_${STAMP}.log 2>/dev/null || true
    git add results_live logs 2>/dev/null || true
    git add results 2>/dev/null || true
    if git diff --cached --quiet 2>/dev/null; then
        echo "[GIT] nothing new this stage."
        return 0
    fi
    git commit -q -m "Tier B ${STAMP}: stage results + logs (auto-push)" || true
    if git push "${PUSH_URL}" HEAD:main 2>/dev/null; then
        echo "[GIT] stage pushed to GitHub main."
    else
        git pull --rebase >/dev/null 2>&1 || true
        git push "${PUSH_URL}" HEAD:main 2>/dev/null && echo "[GIT] stage pushed (after rebase)." \
            || echo "[GIT][WARN] push failed - data stays local in ~/Swarm-coord-in-ddil"
    fi
}
trap push_stage EXIT
trap 'push_stage; exit 143' TERM INT

# --- 1. launch cluster (fixed launcher: flashinfer sampler off, staggered) ----
mkdir -p results_live logs
if [ "${SKIP_TIER_B:-0}" = "1" ]; then
    echo "[TIER B] SKIP_TIER_B=1 - exiting."
    exit 0
fi

if bash launch_vllm_cluster.sh; then
    push_stage   # startup logs on GitHub immediately, success or not
else
    echo "[TIER B][FAIL] cluster did not start - startup logs pushed for diagnosis."
    push_stage
    exit 1
fi

# --- 2. live benchmark validation ---------------------------------------------
echo "[TIER B] live benchmark (2 seeds x 3 drops x 3 protocols)..."
python empirical_ddil_simulation.py --mode benchmark \
    --seeds 42 43 --drop-rates 0.0 0.4 0.8 --nodes 50 --duration 100 \
    --csv-out results_live/benchmark_live_validation.csv \
    2>&1 | tee logs/live_benchmark.log
push_stage

# --- 3. live robustness validation --------------------------------------------
echo "[TIER B] live injection robustness..."
python empirical_ddil_simulation.py --mode robustness \
    --seeds 42 43 \
    --csv-out results_live/robustness_live_validation.csv \
    2>&1 | tee logs/live_robustness.log
push_stage

# --- 4. shutdown + done --------------------------------------------------------
pkill -f api_server 2>/dev/null || true
sleep 3
echo ""
echo "=================================================================="
echo " TIER B COMPLETE - $(date)"
echo " Live CSVs : results_live/*.csv  (auto-pushed)"
echo " Audit     : 'llm_fallbacks' column must be ~0 for genuine live LLM"
echo "=================================================================="
