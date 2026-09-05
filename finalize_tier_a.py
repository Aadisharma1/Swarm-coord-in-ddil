#!/usr/bin/env python3
"""
finalize_tier_a.py — rebuild ALL Tier A outputs from existing per-run CSVs.
Zero simulation. Use when suites completed but a post-processing crash
(e.g., the render_ablation_plot NameError) killed the script before plots,
LaTeX, verification, or the GitHub push happened.

Usage:
    python finalize_tier_a.py                     # reads ./results
    python finalize_tier_a.py --results-dir path  # custom CSV dir
"""

import argparse
import csv
import os
import sys

import empirical_ddil_simulation as sim


def load_rows(path):
    """Loads a CSV and coerces every value: int -> float -> original string.
    DictReader returns strings; the render/aggregation helpers expect numbers."""
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        for raw in csv.DictReader(f):
            row = {}
            for k, v in raw.items():
                try:
                    row[k] = int(v)
                except (TypeError, ValueError):
                    try:
                        row[k] = float(v)
                    except (TypeError, ValueError):
                        row[k] = v
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results-dir', default='results')
    args = ap.parse_args()
    rd = args.results_dir

    expected = {
        'benchmark_10seeds.csv': 271,   # 10 seeds x 9 drops x 3 protocols + header
        'ablation_10seeds.csv': 451,    # 10 seeds x 9 drops x 5 variants + header
        'sensitivity_10seeds.csv': 91,  # 3 thetas x 3 drops x 10 seeds + header
        'robustness_10seeds.csv': 51,   # 5 rates x 10 seeds + header
    }

    ok = True
    for name, exp in expected.items():
        p = os.path.join(rd, name)
        if not os.path.exists(p):
            print(f"[MISSING] {p}")
            ok = False
            continue
        n = sum(1 for _ in open(p, encoding='utf-8'))
        status = 'OK' if n >= exp else f'SHORT (have {n}, want {exp})'
        if n < exp:
            ok = False
        print(f"[CHECK] {name}: {n} lines — {status}")
    if not ok:
        print("[ABORT] one or more CSVs missing/short — rerun the affected suite instead.")
        sys.exit(1)

    bench = load_rows(os.path.join(rd, 'benchmark_10seeds.csv'))
    abl = load_rows(os.path.join(rd, 'ablation_10seeds.csv'))
    seeds = sorted({int(r['seed']) for r in bench})
    drops_b = sorted({float(r['drop_rate']) for r in bench})
    drops_a = sorted({float(r['drop_rate']) for r in abl})

    sim.render_benchmark_plots(bench, seeds, drops_b)
    sim.render_ablation_plot(abl, drops_a)

    # LaTeX severe-point tables (stdout; the DOCX generator computes its own tables)
    def agg(rows, field, scale, **filt):
        import statistics
        vals = [float(r[field]) * scale for r in rows
                if all((r.get(k) == v) if isinstance(v, str) else abs(float(r[k]) - v) < 1e-9
                       for k, v in filt.items())]
        return statistics.mean(vals), sim.ci95_halfwidth(vals)

    severe = max(drops_b)
    # The exporter expects FRACTIONS for pct fields (it scales by 100 internally)
    # and Joules for energy (it scales by 1/1000 internally). CSV stores percent
    # and kJ, hence the 0.01 / 1000 converters.
    fields = (('sync', ('sync_pct', 0.01)), ('delivery', ('delivery_pct', 0.01)),
              ('bytes', ('delivered_bytes', 1)), ('energy', ('energy_kj', 1000)), ('dpr', ('dpr_pct', 0.01)))
    g = {k: agg(bench, f, s, mode='gossip', drop_rate=severe) for k, (f, s) in fields}
    e = {k: agg(bench, f, s, mode='epidemic', drop_rate=severe) for k, (f, s) in fields}
    a = {k: agg(bench, f, s, mode='agentic', drop_rate=severe) for k, (f, s) in fields}
    sim.export_latex_booktabs_table(g, e, a, drop_rate_pct=severe * 100)

    print("\n[TIER A FINALIZE] plots regenerated, CSVs verified.")
    print("[NEXT] bash run_all_dgx.sh  -> resume guards skip the sweep, push CSVs to GitHub,")
    print("       then run Tier B live validation + rebuild the manuscript.")


if __name__ == '__main__':
    main()
