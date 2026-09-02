#!/usr/bin/env bash
# ==============================================================================
# InCIS 2027 — Full GPU Experiment Runner
# ==============================================================================
# One-shot script: launches 8x vLLM endpoints, waits for readiness, then runs
# the full benchmark, ablation, sensitivity, and robustness suites with 10 seeds.
# All output is logged and timestamped.
#
# Usage:  bash run_gpu_experiments.sh [NUM_SEEDS] [DURATION]
#   e.g.  bash run_gpu_experiments.sh 10 100
# ==============================================================================

set -euo pipefail

NUM_SEEDS="${1:-10}"
DURATION="${2:-100}"
MODEL_NAME="meta-llama/Meta-Llama-3-8B-Instruct"
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
    echo "  GPU ${GPU_ID} PID: ${VLLM_PIDS[-1]}"
done

echo "[PHASE 1] All 8 vLLM processes started. Waiting for endpoints to be ready..."

# Wait for all 8 endpoints to respond
MAX_WAIT=300  # 5 minutes
WAIT_INTERVAL=5
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
    echo "  ✓ Port ${PORT} ready (waited ${ELAPSED}s)"
done

echo "[PHASE 1] All 8 vLLM endpoints are live."
echo ""

# ==============================================================================
# PHASE 2: Run Full Benchmark Suite (Gossip vs Epidemic vs Agentic)
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
# PHASE 3: Run Full Ablation Suite
# ==============================================================================
echo "[PHASE 3] Running Ablation Suite (${NUM_SEEDS} seeds x 9 drops x 5 variants)..."
python3 empirical_ddil_simulation.py \
    --mode ablation \
    --seeds ${SEEDS} \
    --nodes 50 \
    --duration ${DURATION} \
    2>&1 | tee "${LOG_DIR}/ablation.log"

echo "[PHASE 3] Ablation complete."
echo ""

# ==============================================================================
# PHASE 4: Run IPS Threshold Sensitivity
# ==============================================================================
echo "[PHASE 4] Running IPS Threshold Sensitivity (theta=0.90, 0.95, 0.98)..."
python3 empirical_ddil_simulation.py \
    --mode sensitivity \
    --seeds ${SEEDS} \
    --nodes 50 \
    --duration ${DURATION} \
    2>&1 | tee "${LOG_DIR}/sensitivity.log"

echo "[PHASE 4] Sensitivity complete."
echo ""

# ==============================================================================
# PHASE 5: Run Hallucination Injection Robustness
# ==============================================================================
echo "[PHASE 5] Running Hallucination Injection Robustness..."
python3 empirical_ddil_simulation.py \
    --mode robustness \
    --seeds ${SEEDS} \
    2>&1 | tee "${LOG_DIR}/robustness.log"

echo "[PHASE 5] Robustness complete."
echo ""

# ==============================================================================
# PHASE 6: Collect & Archive Results
# ==============================================================================
echo "[PHASE 6] Collecting results..."

# Move generated plots into results dir
for f in fig_sync_vs_drop.png fig_energy_vs_drop.png fig_ablation_sync.png; do
    if [ -f "${f}" ]; then
        cp "${f}" "${RESULTS_DIR}/"
        echo "  Copied ${f} -> ${RESULTS_DIR}/"
    fi
done

# Copy the CSV if it exists at default location too
if [ -f "ddil_results.csv" ]; then
    cp ddil_results.csv "${RESULTS_DIR}/ddil_results_default.csv"
fi

# Capture GPU info
nvidia-smi > "${RESULTS_DIR}/gpu_info.txt" 2>&1 || true
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv >> "${RESULTS_DIR}/gpu_info.txt" 2>&1 || true

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
echo "    - benchmark_${NUM_SEEDS}seeds.csv"
echo "    - fig_sync_vs_drop.png"
echo "    - fig_energy_vs_drop.png"
echo "    - fig_ablation_sync.png"
echo "    - gpu_info.txt"
echo "    - logs/benchmark.log"
echo "    - logs/ablation.log"
echo "    - logs/sensitivity.log"
echo "    - logs/robustness.log"
echo "=============================================================="
