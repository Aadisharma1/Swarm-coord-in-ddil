#!/usr/bin/env bash
# ==============================================================================
# vLLM 8x A100 Cluster Launch + Readiness Wait + Warmup
# ==============================================================================
# GPU i -> port 8001+i. Blocks until ALL endpoints answer /v1/models, then sends
# one warmup completion per endpoint (absorbs CUDA-graph capture so the first
# simulation broadcasts don't hit the 2s inference timeout).
#
# Usage:
#   export HF_TOKEN=hf_...        # gated model, or: huggingface-cli login
#   bash launch_vllm_cluster.sh
# ==============================================================================
set -euo pipefail

MODEL_NAME="${VLLM_MODEL_NAME:-meta-llama/Meta-Llama-3-8B-Instruct}"
GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEM:-0.9}"
MAX_MODEL_LEN="${VLLM_MAX_LEN:-2048}"
BASE_PORT=8001
NUM_GPUS="${NUM_GPUS:-8}"

echo "=================================================================="
echo " 8x A100 vLLM Cluster | Model: ${MODEL_NAME}"
echo "=================================================================="

if [ -z "${HF_TOKEN:-}" ] && [ ! -f "$HOME/.cache/huggingface/token" ]; then
    echo "[ERROR] ${MODEL_NAME} is gated. export HF_TOKEN=hf_... (approved access) or run: huggingface-cli login"
    exit 1
fi

PIDS=()
for GPU_ID in $(seq 0 $((NUM_GPUS - 1))); do
    PORT=$((BASE_PORT + GPU_ID))
    LOG_FILE="vllm_node_gpu_${GPU_ID}.log"
    echo "[CLUSTER] GPU ${GPU_ID} -> http://localhost:${PORT}/v1 (log: ${LOG_FILE})"
    CUDA_VISIBLE_DEVICES=${GPU_ID} python3 -m vllm.entrypoints.openai.api_server \
        --model "${MODEL_NAME}" \
        --port "${PORT}" \
        --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
        --max-model-len "${MAX_MODEL_LEN}" \
        --trust-remote-code \
        > "${LOG_FILE}" 2>&1 &
    PIDS+=($!)
done

echo "[CLUSTER] Waiting for readiness (model loading can take 10-20 min for 8 replicas)..."
for GPU_ID in $(seq 0 $((NUM_GPUS - 1))); do
    PORT=$((BASE_PORT + GPU_ID))
    ELAPSED=0
    until curl -sf "http://localhost:${PORT}/v1/models" > /dev/null 2>&1; do
        if [ ${ELAPSED} -ge 1800 ]; then
            echo "[ERROR] Port ${PORT} not ready after 1800s. Check vllm_node_gpu_${GPU_ID}.log"
            exit 1
        fi
        sleep 10; ELAPSED=$((ELAPSED + 10))
    done
    echo "[READY] port ${PORT} (${ELAPSED}s)"
done

echo "[CLUSTER] Warmup completions..."
for GPU_ID in $(seq 0 $((NUM_GPUS - 1))); do
    PORT=$((BASE_PORT + GPU_ID))
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:${PORT}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${MODEL_NAME}\", \"messages\": [{\"role\": \"user\", \"content\": \"Reply with: {\"}], \"max_tokens\": 8}" || true)
    echo "  warmup port ${PORT}: HTTP ${CODE}"
done

echo "=================================================================="
echo " All ${NUM_GPUS} endpoints live and warm. PIDs: ${PIDS[*]}"
echo " Shutdown later: kill ${PIDS[*]}   (or: pkill -f api_server)"
echo "=================================================================="
