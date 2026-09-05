# DGX Experiment Runbook — InCIS 2027 (8x A100 40GB)

All heavy compute runs on the DGX. The laptop runs nothing beyond unit tests.

**Why two tiers:** one live-LLM inference call takes ~0.15–0.4 s; a full live
benchmark sweep is 10 seeds x 9 drop rates x ~2,500 compressions = ~225,000
calls = 9–25 h single-process. So the **full statistical sweep runs in
deterministic CPU mode** (schema-identical quantizer, exact reproducibility —
this is the manuscript's stated data policy), and **live vLLM is used for a
validation subset** proving fallback and live inference behave identically
under the IPS gate. Every run logs an `llm_fallbacks` counter so live runs can
be audited for silent degradation.

---

## Setup (once, ~10 min)

```bash
# copy repo to DGX
rsync -av --exclude '.git' --exclude 'results*' --exclude '__pycache__' \
    ./ user@dgx:~/incis/
ssh user@dgx
cd ~/incis

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # simpy, networkx, matplotlib, aiohttp,
                                         # pandas, python-docx, latex2mathml,
                                         # mathml2omml, pytest

# gated model access
export HF_TOKEN=hf_xxx                   # needs approved Llama-3 access
huggingface-cli login                    # (either one works)

# sanity — CPU only, <2 min
python3 test_simulation_units.py         # expect: [ALL UNIT TESTS PASSED]
```

---

## EXP 1 — Tier A: full statistical sweep (CPU-mode, ~35–70 min, parallel)

Primary numbers for every table in the paper. 10 paired seeds (42–51),
N=50, Watts-Strogatz k=6, calibrated Gilbert-Elliott channel, 0–80% drops.

```bash
tmux new -s incis
bash run_cpu_sweep.sh 10 100
# detach: Ctrl-b d    reattach: tmux attach -t incis
```

Produces:
| File | Feeds |
|---|---|
| `results/benchmark_10seeds.csv` | Tables 2–3, Figures 1–2 |
| `results/ablation_10seeds.csv` | Table 4, Figure 3 |
| `results/sensitivity_10seeds.csv` | Table 5 |
| `results/robustness_10seeds.csv` | Table 6 |
| `fig_sync_vs_drop.png` / `fig_energy_vs_drop.png` / `fig_ablation_sync.png` | Figures 1–4 |

Monitor: `tail -f logs_cpu_*/benchmark.log`

---

## EXP 2 — Tier B: live vLLM validation subset (~2–3 h, GPUs)

Proves the deterministic-mode results carry over to live Llama-3-8B inference.
Small grid on purpose (full live sweep would be 9–25 h — see Tier C).

```bash
tmux new -s live
bash launch_vllm_cluster.sh              # 8 endpoints, blocks until warm
                                         # (~10–20 min model loading)

# validation benchmark: 2 seeds x 3 drop rates x 3 protocols (~1–1.5 h)
python3 empirical_ddil_simulation.py --mode benchmark \
    --seeds 42 43 --drop-rates 0.0 0.4 0.8 --nodes 50 --duration 100 \
    --csv-out results_live/benchmark_live_validation.csv \
    2>&1 | tee live_benchmark.log

# live hallucination-injection test: 3 rates x 2 seeds (~1 h)
python3 empirical_ddil_simulation.py --mode robustness \
    --seeds 42 43 \
    --csv-out results_live/robustness_live_validation.csv \
    2>&1 | tee live_robustness.log

# shutdown
pkill -f api_server
```

**Acceptance check:** in the live CSVs, `llm_fallbacks` must be ~0 (any large
value = endpoints weren't ready / timed out; rerun with the preflight). Then
compare live vs. CPU-mode DPR / sync / bytes at matching drop rates — the
manuscript cites this agreement.

---

## EXP 3 — Tier C (OPTIONAL, overnight): fully-live benchmark

Only if you want live-LLM headline numbers instead of CPU-mode ones.
Single-process ~10–25 h (inference-call bound):

```bash
tmux new -s fulllive
bash run_gpu_experiments.sh 10 100       # launches vLLM + full 10-seed live sweep
                                         # + all suites, then shuts GPUs down
```

Skip unless Tier B shows discrepancies — the paper is written so CPU-mode is
the primary, reproducible evidence.

---

## EXP 4 — Rebuild the manuscript (seconds, CPU)

```bash
# Tier A CSVs are the primary source:
python3 migrate_to_incis_final.py
# -> utsa/pdrone_InCIS_2027_Submission.docx
#    (fails loudly if any results/*.csv is missing — never builds without data)
```

Copy-back to laptop:
```bash
scp -r user@dgx:~/incis/results user@dgx:~/incis/fig_*.png user@dgx:~/incis/logs_cpu_* \
    ./laptop:path/to/convert/
```

---

## Experiment inventory (what each produces)

| # | Suite | Grid | Runtime (DGX) | Output |
|---|-------|------|---------------|--------|
| 1a | Benchmark: Gossip vs Epidemic vs Agentic SLM | 10 seeds x 9 drops x 3 protocols, N=50 | 35–60 min (parallel CPU) | benchmark CSV, Figs 1–2 |
| 1b | Ablation: Full / A1 no-link-memory / A2 no-compression / A3 no-relay / A4 no-gate | 10 seeds x 9 drops x 5 variants | 20–30 min | ablation CSV, Fig 3 |
| 1c | IPS threshold sensitivity | 3 thetas x 10 seeds x 3 drops | ~5 min | sensitivity CSV |
| 1d | Injection robustness (measured gate recall/FAR) | 5 rates x 10 seeds @ 40% loss | ~4 min | robustness CSV |
| 2a | Live-vLLM validation benchmark | 2 seeds x 3 drops x 3 protocols | 1–1.5 h (8x A100) | live CSV + agreement check |
| 2b | Live-vLLM injection robustness | 3 rates x 2 seeds | ~1 h | live CSV |
| 3  | (optional) fully-live full sweep | 10 seeds x 9 drops | 10–25 h | replaces Tier A numbers |

## Checks before rebuilding the manuscript

1. `wc -l results/*.csv` — benchmark = 1 + 10x9x3 = 271 rows; ablation = 1 + 450;
   sensitivity = 1 + 90; robustness = 1 + 50.
2. Live CSVs: `llm_fallbacks` column ~0.
3. `python3 migrate_to_incis_final.py` prints measured payload facts and
   headline aggregates — confirm they match the CSVs.
