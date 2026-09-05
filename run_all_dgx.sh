#!/usr/bin/env bash
# ==============================================================================
# InCIS 2027 — ONE-SHOT DGX RUNNER (paste-and-forget, phone-friendly)
# ==============================================================================
# Expects in env: HF_TOKEN (gated Llama-3), WANDB_API_KEY (optional but wanted).
# Does EVERYTHING: env check -> venv+deps -> HF auth -> unit tests -> Tier A
# CPU sweep (10 seeds, 4 suites, parallel) -> Tier B live vLLM validation ->
# shutdown GPUs -> rebuild manuscript from CSVs -> summary.
#
# Idempotent-ish: safe to re-run; logs to run_all_<timestamp>.log and tee's
# everything. Run inside tmux/nohup — total wall ~4-6 h (Tier B dominates).
# ==============================================================================
set -uo pipefail

STAMP=$(date +%Y%m%d_%H%M%S)
MASTER_LOG="run_all_${STAMP}.log"
exec > >(tee -a "${MASTER_LOG}") 2>&1

echo "=================================================================="
echo " InCIS 2027 one-shot DGX run — started $(date)"
echo " Node: $(hostname) | GPUs: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -8 | tr '\n' ';')"
echo "=================================================================="

# --- 0. Env checks -----------------------------------------------------------
: "${HF_TOKEN:?export HF_TOKEN=hf_... (gated Llama-3 access). Aborting.}"
export HF_TOKEN
if [ -z "${WANDB_API_KEY:-}" ]; then
    echo "[WARN] WANDB_API_KEY not set — runs will skip W&B logging."
else
    export WANDB_API_KEY
    export WANDB_PROJECT="${WANDB_PROJECT:-incis2027-ddil}"
fi

PYTHON="${PYTHON:-python3}"
command -v "${PYTHON}" >/dev/null || { echo "[ERROR] python3 not found"; exit 1; }

# --- 1. Virtualenv + dependencies --------------------------------------------
if [ ! -d .venv ]; then
    echo "[SETUP] Creating virtualenv..."
    "${PYTHON}" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "[SETUP] Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q || { echo "[SETUP] retrying dependency install..."; pip install -r requirements.txt; }
pip install wandb -q
pip install "huggingface_hub" -q

# --- 2. HF auth (vLLM/huggingface_hub read HF_TOKEN directly; verify it) ------
echo "[HF] Verifying token..."
python - <<'PY' || { echo "[ERROR] HF_TOKEN invalid or lacks gated-model access. Aborting."; exit 1; }
from huggingface_hub import whoami
info = whoami()
print(f"[HF] authenticated as: {info.get('name')}")
PY

# --- 3. Unit tests (fast gate) ------------------------------------------------
echo "[TESTS] Running unit tests..."
python test_simulation_units.py || { echo "[ERROR] unit tests failed. Aborting."; exit 1; }

# --- 4. Tier A: full CPU-mode statistical sweep (4 suites in parallel) --------
echo "[TIER A] Starting CPU-mode 10-seed sweep (parallel suites)..."
bash run_cpu_sweep.sh 10 100 || { echo "[ERROR] Tier A sweep failed — check logs_cpu_*/. Aborting."; exit 1; }

echo "[TIER A] Verifying CSVs..."
for f in results/benchmark_10seeds.csv results/ablation_10seeds.csv \
         results/sensitivity_10seeds.csv results/robustness_10seeds.csv; do
    [ -s "$f" ] || { echo "[ERROR] missing/empty $f"; exit 1; }
    wc -l "$f"
done

# --- 5. Tier B: live vLLM validation (8x A100) --------------------------------
mkdir -p results_live logs

# vLLM is NOT in requirements.txt (only needed for live validation); install
# into the venv if missing. Large wheel (~2 GB with CUDA deps) — a few minutes.
SKIP_TIER_B=0
python -c "import vllm" 2>/dev/null || {
    echo "[TIER B] Installing vLLM (~5-10 min, large wheel)..."
    pip install vllm -q || { echo "[WARN] vLLM install failed — skipping live validation."; SKIP_TIER_B=1; }
}

if [ "${SKIP_TIER_B}" = "1" ]; then
    echo "[TIER B] Skipped (vLLM unavailable)."
elif bash launch_vllm_cluster.sh; then
    echo "[TIER B] Live benchmark validation (2 seeds x 3 drops)..."
    python empirical_ddil_simulation.py --mode benchmark \
        --seeds 42 43 --drop-rates 0.0 0.4 0.8 --nodes 50 --duration 100 \
        --csv-out results_live/benchmark_live_validation.csv \
        2>&1 | tee logs/live_benchmark.log || echo "[WARN] live benchmark failed (see logs) — continuing"

    echo "[TIER B] Live injection-robustness validation..."
    python empirical_ddil_simulation.py --mode robustness \
        --seeds 42 43 \
        --csv-out results_live/robustness_live_validation.csv \
        2>&1 | tee logs/live_robustness.log || echo "[WARN] live robustness failed — continuing"

    pkill -f api_server 2>/dev/null || true
    sleep 3
else
    echo "[WARN] vLLM cluster failed to start — skipping live validation (Tier A results still valid)."
fi

# --- 6. Rebuild manuscript from Tier A data -----------------------------------
echo "[PAPER] Rebuilding manuscript from results CSVs..."
python migrate_to_incis_final.py || { echo "[ERROR] manuscript build failed."; exit 1; }

# --- 7. Auto-push results, figures, manuscript, logs back to GitHub -----------
echo "[GIT] Pushing results back to GitHub..."
git config user.email "aadisharma2808@gmail.com" 2>/dev/null || true
git config user.name "Aadi Sharma (DGX)" 2>/dev/null || true

# Keep machine-only dirs out of the results commit
cat >> .gitignore <<'GI'

# DGX run artifacts (never commit)
.venv/
wandb/
__pycache__/
*.pyc
GI

git add results results_live 2>/dev/null || true
git add -f utsa/pdrone_InCIS_2027_Submission.docx 2>/dev/null || true
git add fig_sync_vs_drop.png fig_energy_vs_drop.png fig_ablation_sync.png 2>/dev/null || true
git add logs_cpu_* logs results_live/*.log run_all_*.log live_*.log 2>/dev/null || true

if git diff --cached --quiet 2>/dev/null; then
    echo "[GIT] Nothing new to commit."
else
    git commit -q -m "DGX results ${STAMP}: Tier A CSVs + live validation + figures + manuscript" || true
    echo "[GIT] Committed results snapshot ${STAMP}."
fi

PUSHED=0
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    echo "[GIT] Using existing gh login for push..."
    gh auth setup-git >/dev/null 2>&1 || true
    git push origin main && PUSHED=1
elif [ -n "${GIT_PUSH_TOKEN:-}" ]; then
    echo "[GIT] Using GIT_PUSH_TOKEN for push..."
    git push "https://x-access-token:${GIT_PUSH_TOKEN}@github.com/Aadisharma1/Swarm-coord-in-ddil.git" HEAD:main && PUSHED=1
else
    echo "[GIT] No gh login and no GIT_PUSH_TOKEN — results remain local on the DGX"
    echo "      (~/Swarm-coord-in-ddil). Push later with: git push origin main"
fi
[ "${PUSHED}" = "1" ] && echo "[GIT] Results, figures, manuscript, and logs are on GitHub main."

# --- 8. Summary ---------------------------------------------------------------
echo ""
echo "=================================================================="
echo " ALL DONE — $(date)"
echo " Master log : ${MASTER_LOG}"
echo " Results    : results/ (4 CSVs) + results_live/ (2 live CSVs)"
echo " Figures    : fig_sync_vs_drop.png fig_energy_vs_drop.png fig_ablation_sync.png"
echo " Manuscript : utsa/pdrone_InCIS_2027_Submission.docx"
echo " W&B        : project '${WANDB_PROJECT:-incis2027-ddil}' (4 suite runs + artifacts)"
echo ""
echo " Fallback audit: check 'llm_fallbacks' column in results_live/*.csv"
echo "   ~0 = live LLM genuine; large = fallback contamination, rerun Tier B."
echo "=================================================================="
