#!/usr/bin/env bash
# ==============================================================================
# vLLM 8x A100 GPU Cluster Launch Script
# ==============================================================================
# Architecture: Pinned 1-to-1 GPU-to-Node Cluster Server Setup
# Description : Launches 8 independent OpenAI-compatible vLLM API server
#               instances on localhost across CUDA devices 0 through 7.
#
# Ports       : 8001, 8002, 8003, 8004, 8005, 8006, 8007, 8008
# Target Model: meta-llama/Meta-Llama-3-8B-Instruct
# ==============================================================================

set -euo pipefail

MODEL_NAME="meta-llama/Meta-Llama-3-8B-Instruct"
GPU_MEMORY_UTILIZATION="0.9"
MAX_MODEL_LEN="2048"
BASE_PORT=8001

echo "=================================================================="
echo "Initializing 8x A100 GPU Cluster for Decentralized Node Inference"
echo "Model: ${MODEL_NAME}"
echo "=================================================================="

# Loop through GPU IDs 0 to 7
for GPU_ID in {0..7}; do
    PORT=$((BASE_PORT + GPU_ID))
    LOG_FILE="vllm_node_gpu_${GPU_ID}.log"

    echo "[CLUSTER] Spawning vLLM Node ${GPU_ID} on CUDA_VISIBLE_DEVICES=${GPU_ID} at http://localhost:${PORT}/v1..."

    CUDA_VISIBLE_DEVICES=${GPU_ID} python3 -m vllm.entrypoints.openai.api_server \
        --model "${MODEL_NAME}" \
        --port ${PORT} \
        --gpu-memory-utilization ${GPU_MEMORY_UTILIZATION} \
        --max-model-len ${MAX_MODEL_LEN} \
        --trust-remote-code \
        > "${LOG_FILE}" 2>&1 &

    PID=$!
    echo "[CLUSTER] Node ${GPU_ID} process launched with PID: ${PID} (Logging to ${LOG_FILE})"
done

echo "------------------------------------------------------------------"
echo "All 8 vLLM endpoints initializing in background."
echo "Endpoints map:"
for GPU_ID in {0..7}; do
    PORT=$((BASE_PORT + GPU_ID))
    echo "  - Node ${GPU_ID} -> http://localhost:${PORT}/v1/chat/completions"
done
echo "=================================================================="
