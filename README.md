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
| `migrate_to_incis_final.py` | InCIS 2027 Word manuscript generator |
| `requirements.txt` | Python dependencies |

## Architecture

- **Gossip Protocol** (Demers et al., 1987): TTL-bounded flooding of raw 450B JSON state
- **Epidemic Routing** (Vahdat & Becker, 2000): Store-and-forward anti-entropy dissemination
- **Agentic SLM** (Proposed): Multi-invariant semantic compression (~85-110B), sender-side IPS verification, 2-hop joint path relay routing