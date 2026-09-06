#!/usr/bin/env bash
# ==============================================================================
# Tier A: CPU-mode exact statistical sweep (PRIMARY paper numbers)
# ==============================================================================
# Runs all 4 experiment suites in PARALLEL background jobs (each is single-core
# Python; the DGX has dozens of cores, so 4 jobs = ~4x wall-time reduction).
# Deterministic mode (DDIL_DISABLE_VLLM=1): schema-identical quantizer, fully
# reproducible, no GPUs required.
#
# Usage:  bash run_cpu_sweep.sh [NUM_SEEDS] [DURATION]
# Output: results/*.csv + fig_sync_vs_drop.png + fig_energy_vs_drop.png +
#         fig_ablation_sync.png + logs/
# Wall time on DGX (10 seeds, N=50, 100t): ~35-70 min total (parallel).
# ==============================================================================
set -euo pipefail

NUM_SEEDS="${1:-10}"
DURATION="${2:-100}"
STAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR="logs_cpu_${STAMP}"
mkdir -p results "${LOG_DIR}"

SEEDS=""
for i in $(seq 0 $((NUM_SEEDS - 1))); do SEEDS="${SEEDS} $((42 + i))"; done
SEEDS=$(echo $SEEDS | xargs)

echo "[TIER A] CPU-mode sweep | seeds: ${SEEDS} | N=50 | duration ${DURATION}t"
export DDIL_DISABLE_VLLM=1

# Suite 1: main benchmark (longest — epidemic flooding dominates)
python3 empirical_ddil_simulation.py --mode benchmark \
    --seeds ${SEEDS} --nodes 50 --duration ${DURATION} \
    --csv-out results/benchmark_${NUM_SEEDS}seeds.csv \
    > "${LOG_DIR}/benchmark.log" 2>&1 &
P1=$!

# Suite 2: ablation (5 architectural variants)
python3 empirical_ddil_simulation.py --mode ablation \
    --seeds ${SEEDS} --nodes 50 --duration ${DURATION} \
    --csv-out results/ablation_${NUM_SEEDS}seeds.csv \
    > "${LOG_DIR}/ablation.log" 2>&1 &
P2=$!

# Suite 3: IPS threshold sensitivity (3 drops)
python3 empirical_ddil_simulation.py --mode sensitivity \
    --seeds ${SEEDS} --nodes 50 --duration ${DURATION} --drop-rates 0.0 0.4 0.8 \
    --csv-out results/sensitivity_${NUM_SEEDS}seeds.csv \
    > "${LOG_DIR}/sensitivity.log" 2>&1 &
P3=$!

# Suite 4: hallucination-injection robustness (at 40% loss)
python3 empirical_ddil_simulation.py --mode robustness \
    --seeds ${SEEDS} --duration ${DURATION} \
    --csv-out results/robustness_${NUM_SEEDS}seeds.csv \
    > "${LOG_DIR}/robustness.log" 2>&1 &
P4=$!

echo "[TIER A] 4 suites launched (PIDs ${P1} ${P2} ${P3} ${P4}). Poll: tail -f ${LOG_DIR}/*.log"
wait ${P1} ${P2} ${P3} ${P4}

echo "[TIER A] All suites done: $(date)"
ls -la results/
echo "[TIER A] Next: CSVs in results/. Paper is built locally by the author (not in this repo); copy results/*.csv to paper_artifacts/ for archival."
