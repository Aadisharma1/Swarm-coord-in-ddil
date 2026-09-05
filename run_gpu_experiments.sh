#!/usr/bin/env bash
# ==============================================================================
# InCIS 2027 — Full GPU Experiment Runner (DGX, 8x A100)
# ==============================================================================
# One-shot script: verifies HF access, launches 8x vLLM endpoints, waits for
# readiness, warms up inference, then runs the benchmark, ablation, sensitivity,
# and robustness suites with 10 seeds. All suites write per-run CSVs; the
# manuscript generator (migrate_to_incis_final.py) reads those CSVs directly.
#
# Usage:  bash run_gpu_experiments.sh [NUM_SEEDS] [DURATION]
#   e.g.  bash run_gpu_experiments.sh 10 100
#
# Estimated wall-clock (8x A100-40GB, Llama-3-8B BF16): benchmark ~4-6 h
# (dominated by ~22,500 live agentic compressions), ablation ~2-3 h,
# sensitivity/robustness ~1-2 h. Run inside tmux/nohup.
# ==============================================================================

set -euo pipefail

NUM_SEEDS="${1:-10}"
DURATION="${2:-100}"
MODEL_NAME="${VLLM_MODEL_NAME:-meta-llama/Meta-Llama-3-8B-Instruct}"
BASE_PORT=8001
GPU_MEM_UTIL="0.9"
MAX_MODEL_LEN="2048"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="results_${TIMESTAMP}"
LOG_DIR="${RESULTS_DIR}/logs"

mkdir -p "${RESULTS_DIR}" "${LOG_DIR}"

echo "=============================================================="
echo "  InCIS 2027 GPU Experiment Runner"
echo "  Seeds: ${NUM_SEEDS} | Duration: ${DURATION}t | Model: ${MODEL_NAME}"
echo "  Results Dir: ${RESULTS_DIR}"
echo "  Started: $(date)"
echo "=============================================================="

# ==============================================================================
# PHASE 0: Environment Preflight (fail fast with actionable messages)
# ==============================================================================
command -v curl >/dev/null 2>&1 || { echo "[ERROR] curl not found"; exit 1; }

if [ -z "${HF_TOKEN:-}" ]; then
    if [ -f "$HOME/.cache/huggingface/token" ]; then
        echo "[PHASE 0] Using cached Hugging Face token."
    else
        echo "[ERROR] HF_TOKEN is not set and no cached token found."
        echo "        ${MODEL_NAME} is a gated model: export HF_TOKEN=hf_... (with approved access)"
        echo "        or run: huggingface-cli login"
        exit 1
    fi
else
    echo "[PHASE 0] HF_TOKEN present."
fi

# Generate seed list: 42, 43, ..., 42+NUM_SEEDS-1
SEEDS=""
for i in $(seq 0 $((NUM_SEEDS - 1))); do
    SEEDS="${SEEDS} $((42 + i))"
done
SEEDS=$(echo $SEEDS | xargs)  # trim
echo "[CONFIG] Seeds: ${SEEDS}"

# ==============================================================================
# PHASE 1: Launch vLLM Cluster
# ==============================================================================
echo ""
echo "[PHASE 1] Launching 8x vLLM endpoints..."

VLLM_PIDS=()
for GPU_ID in $(seq 0 7); do
    PORT=$((BASE_PORT + GPU_ID))
    VLLM_LOG="${LOG_DIR}/vllm_gpu_${GPU_ID}.log"

    echo "  Spawning GPU ${GPU_ID} on port ${PORT}..."
    CUDA_VISIBLE_DEVICES=${GPU_ID} python3 -m vllm.entrypoints.openai.api_server \
        --model "${MODEL_NAME}" \
        --port ${PORT} \
        --gpu-memory-utilization ${GPU_MEM_UTIL} \
        --max-model-len ${MAX_MODEL_LEN} \
        --trust-remote-code \
        > "${VLLM_LOG}" 2>&1 &

    VLLM_PIDS+=($!)
done

echo "[PHASE 1] All 8 vLLM processes started. Waiting for endpoints to be ready..."

# Wait for all 8 endpoints to respond (model loading of 8 replicas can take
# 10-20 min from network storage; do not give up at 5)
MAX_WAIT=1800  # 30 minutes
WAIT_INTERVAL=10
for GPU_ID in $(seq 0 7); do
    PORT=$((BASE_PORT + GPU_ID))
    URL="http://localhost:${PORT}/v1/models"
    ELAPSED=0
    while ! curl -s "${URL}" > /dev/null 2>&1; do
        if [ ${ELAPSED} -ge ${MAX_WAIT} ]; then
            echo "[ERROR] vLLM endpoint on port ${PORT} not ready after ${MAX_WAIT}s. Check ${LOG_DIR}/vllm_gpu_${GPU_ID}.log"
            exit 1
        fi
        sleep ${WAIT_INTERVAL}
        ELAPSED=$((ELAPSED + WAIT_INTERVAL))
    done
    echo "  OK: Port ${PORT} ready (waited ${ELAPSED}s)"
done

# Warmup: one completion per endpoint absorbs CUDA-graph capture and engine
# warmup; without this the first simulation broadcasts can exceed the 2s
# inference timeout and silently fall back to the deterministic quantizer.
echo "[PHASE 1] Warming up endpoints..."
for GPU_ID in $(seq 0 7); do
    PORT=$((BASE_PORT + GPU_ID))
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:${PORT}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL_NAME}\", \"messages\": [{\"role\": \"user\", \"content\": \"Reply with: {\"}], \"max_tokens\": 8}" || true)
    echo "  Warmup port ${PORT}: HTTP ${HTTP_CODE}"
done

echo "[PHASE 1] All 8 vLLM endpoints are live and warm."
echo ""

# ==============================================================================
# PHASE 2: Full Benchmark Suite (Gossip vs Epidemic vs Agentic)
# ==============================================================================
echo "[PHASE 2] Running Full Benchmark Suite (${NUM_SEEDS} seeds x 9 drops x 3 protocols)..."
python3 empirical_ddil_simulation.py \
    --mode benchmark \
    --seeds ${SEEDS} \
    --nodes 50 \
    --duration ${DURATION} \
    --csv-out "${RESULTS_DIR}/benchmark_${NUM_SEEDS}seeds.csv" \
    2>&1 | tee "${LOG_DIR}/benchmark.log"

echo "[PHASE 2] Benchmark complete."
echo ""

# ==============================================================================
# PHASE 3: Full Ablation Suite
# ==============================================================================
echo "[PHASE 3] Running Ablation Suite (${NUM_SEEDS} seeds x 9 drops x 5 variants)..."
python3 empirical_ddil_simulation.py \
    --mode ablation \
    --seeds ${SEEDS} \
    --nodes 50 \
    --duration ${DURATION} \
    --csv-out "${RESULTS_DIR}/ablation_${NUM_SEEDS}seeds.csv" \
    2>&1 | tee "${LOG_DIR}/ablation.log"

echo "[PHASE 3] Ablation complete."
echo ""

# ==============================================================================
# PHASE 4: IPS Threshold Sensitivity
# ==============================================================================
echo "[PHASE 4] Running IPS Threshold Sensitivity (theta=0.90, 0.95, 0.98)..."
python3 empirical_ddil_simulation.py \
    --mode sensitivity \
    --seeds ${SEEDS} \
    --nodes 50 \
    --duration ${DURATION} \
    --csv-out "${RESULTS_DIR}/sensitivity_${NUM_SEEDS}seeds.csv" \
    2>&1 | tee "${LOG_DIR}/sensitivity.log"

echo "[PHASE 4] Sensitivity complete."
echo ""

# ==============================================================================
# PHASE 5: Hallucination Injection Robustness
# ==============================================================================
echo "[PHASE 5] Running Hallucination Injection Robustness..."
python3 empirical_ddil_simulation.py \
    --mode robustness \
    --seeds ${SEEDS} \
    --csv-out "${RESULTS_DIR}/robustness_${NUM_SEEDS}seeds.csv" \
    2>&1 | tee "${LOG_DIR}/robustness.log"

echo "[PHASE 5] Robustness complete."
echo ""

# ==============================================================================
# PHASE 6: Collect & Archive Results
# ==============================================================================
echo "[PHASE 6] Collecting results..."

for f in fig_sync_vs_drop.png fig_energy_vs_drop.png fig_ablation_sync.png; do
    if [ -f "${f}" ]; then
        cp "${f}" "${RESULTS_DIR}/"
        echo "  Copied ${f} -> ${RESULTS_DIR}/"
    fi
done

nvidia-smi > "${RESULTS_DIR}/gpu_info.txt" 2>&1 || true
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv >> "${RESULTS_DIR}/gpu_info.txt" 2>&1 || true

# Fallback-contamination audit: count agentic fallback usage across the benchmark log
FB=$(grep -o "llm_fallbacks" "${LOG_DIR}/benchmark.log" | wc -l || true)
echo "[PHASE 6] Note: verify agentic llm_fallbacks column in the CSV is ~0 (fallback contamination means live LLM was bypassed)."

# ==============================================================================
# PHASE 7: Shutdown vLLM Cluster
# ==============================================================================
echo "[PHASE 7] Shutting down vLLM processes..."
for PID in "${VLLM_PIDS[@]}"; do
    kill "${PID}" 2>/dev/null || true
done
sleep 2
echo "[PHASE 7] vLLM processes terminated."

# ==============================================================================
# DONE
# ==============================================================================
echo ""
echo "=============================================================="
echo "  EXPERIMENT RUN COMPLETE"
echo "  Finished: $(date)"
echo "  Results:  ${RESULTS_DIR}/"
echo "    - benchmark_${NUM_SEEDS}seeds.csv     (Tables 2-3 + Figs 1-2)"
echo "    - ablation_${NUM_SEEDS}seeds.csv     (Table 4 + Fig 3)"
echo "    - sensitivity_${NUM_SEEDS}seeds.csv  (Table 5)"
echo "    - robustness_${NUM_SEEDS}seeds.csv   (Table 6)"
echo "  Next: copy the 4 CSVs + 3 PNGs next to migrate_to_incis_final.py and run"
echo "        'python migrate_to_incis_final.py' to regenerate the manuscript."
echo "=============================================================="
