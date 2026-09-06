# Decentralized Agentic SLM Coordination for DDIL Swarm Resilience

**InCIS 2027 Track 02: Resilient Digital Systems for the Future**

## Quick Start (8x A100 GPU Cluster)

```bash
# 1. Clone
git clone https://github.com/Aadisharma1/Swarm-coord-in-ddil.git && cd Swarm-coord-in-ddil

# 2. Install dependencies (vllm must already be installed on the GPU machine)
pip install -r requirements.txt

# 3. Run everything (launches vLLM, benchmarks, ablations, sensitivity, robustness)
bash run_gpu_experiments.sh 10 100
```

## Files

| File | Purpose |
|---|---|
| `empirical_ddil_simulation.py` | Core simulation: Gossip, Epidemic, Agentic SLM with DPR, IPS, Gilbert-Elliott |
| `run_gpu_experiments.sh` | One-shot GPU runner: launches vLLM cluster, runs all experiments, logs everything |
| `launch_vllm_cluster.sh` | Standalone vLLM 8x A100 launcher |
| `test_simulation_units.py` | Unit tests for IPS, DPR, relay, channel model |
| `gen_paper_figures.py` | Regenerates the architecture and analytical figures from code (deterministic) |
| `finalize_tier_a.py` | Regenerates plots and LaTeX tables from existing per-run CSVs (no re-simulation) |
| `run_cpu_sweep.sh` | CPU-mode sweep driver: 4 parallel suites, 10 seeds, deterministic fallback |
| `run_tier_b.sh` | Live vLLM validation runner with per-stage incremental GitHub pushes |
| `requirements.txt` | Python dependencies |

## Reproducing the Results

The four per-run CSVs in `results/` are the canonical artifact of the experiments:

- `results/benchmark_10seeds.csv` — Gossip vs Epidemic vs Agentic SLM, 10 seeds × 9 drop rates × 3 protocols
- `results/ablation_10seeds.csv` — 5 architectural variants, 10 seeds × 9 drop rates
- `results/sensitivity_10seeds.csv` — IPS threshold sweep at three drop rates
- `results/robustness_10seeds.csv` — Hallucination-injection robustness at five injection rates

The script `finalize_tier_a.py` rebuilds the three result plots and the severe-regime LaTeX table from these CSVs without re-running any simulation. The plots it produces are the same ones embedded in the paper.

## Paper

The Word manuscript is built locally by the author from the canonical CSVs in `results/` and is not committed to this repository. To obtain the paper, contact the author directly. All numerical claims in the paper are reproducible from the CSVs and the simulation code in this repository.

## Architecture

- **Gossip Protocol** (Demers et al., 1987): TTL-bounded flooding of raw 450B JSON state
- **Epidemic Routing** (Vahdat & Becker, 2000): Store-and-forward anti-entropy dissemination
- **Agentic SLM** (Proposed): Multi-invariant semantic compression (~85-110B), sender-side IPS verification, 2-hop joint path relay routing