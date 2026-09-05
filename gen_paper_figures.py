#!/usr/bin/env python3
"""
Generates the system-architecture figure for the InCIS-2027 manuscript:
task-oriented semantic compression pipeline with sender-side IPS verification,
byte-scaled Gilbert-Elliott burst channel, receiver-side validation, and DPR.

Author: Aadi Sharma, September 2026
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = r"C:\Users\Aadi Sharma\OneDrive\Desktop\convert"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": "#333333",
    "axes.linewidth": 1.0,
})


def figure1_architecture():
    fig, ax = plt.subplots(figsize=(10.0, 6.0), dpi=300)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 62)
    ax.axis("off")

    C_A = "#d7e9f7"    # perception
    C_B = "#cde9dd"    # SLM compression
    C_C = "#fdebd0"    # verification gate
    C_D = "#e8e6f4"    # link memory / relay
    C_R1 = "#d8e6cf"   # receiver validation
    C_R2 = "#eef4ea"   # receiver decision
    C_EDGE = "#40679e"
    C_GREEN = "#3c6e47"
    C_TXT = "#1a1a2e"

    def box(x, y, w, h, face, edge=C_EDGE, lw=1.4):
        b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6,rounding_size=1.4",
                           fc=face, ec=edge, lw=lw, zorder=2)
        ax.add_patch(b)

    def arrow(x1, y1, x2, y2, color=C_EDGE, lw=1.8, ls="-"):
        a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=15,
                            color=color, lw=lw, linestyle=ls, zorder=3, shrinkA=2, shrinkB=2)
        ax.add_patch(a)

    def label(x, y, text, size=9, weight="normal", color=C_TXT, ha="center"):
        ax.text(x, y, text, ha=ha, va="center", fontsize=size, fontweight=weight, color=color, zorder=4)

    # ---------------- Sender column (left) ----------------
    ax.text(23.5, 59.5, "Sender Node $N_i$", fontsize=11, fontweight="bold", ha="center", color=C_TXT)

    box(1.5, 48, 44, 8.5, C_A)
    label(23.5, 54.4, "Perception & State Generation", 9.5, "bold")
    label(23.5, 50.6, "Raw state $S_i(t)=\\langle seq,\\, i,\\, t,\\, v,\\, E,\\, \\phi,\\, W \\rangle$   (~200 B JSON)", 8.2)

    box(1.5, 35.5, 44, 8.5, C_B)
    label(23.5, 41.9, "SLM Task-Oriented Compression", 9.5, "bold")
    label(23.5, 38.1, "$\\hat{S}_i(t)=f_{\\mathrm{SLM}}(S_i(t))$:  pos, vel, hdg, bat, pri, st   (~104 B)", 8.2)

    box(1.5, 23, 44, 8.5, C_C, edge="#b0722c")
    label(23.5, 29.4, "Sender-Side IPS Verification Gate", 9.5, "bold", "#7a4a12")
    label(23.5, 25.6, "$\\mathrm{IPS}=1-\\frac{1}{5}\\sum_k e_k \\geq \\theta_{IPS}=0.95$ and each $e_k<0.25$ — failed tokens suppressed", 7.8, color="#7a4a12")

    box(1.5, 12.5, 44, 8.0, C_D)
    label(23.5, 18.3, "EMA Link Memory + 2-Hop Joint Relay", 9.5, "bold")
    label(23.5, 14.7, "$L_{i,k}(t) \\leftarrow 0.9\\,L_{i,k}(t-1)+0.1\\,A_{i,k}$;   relay $m^*=\\arg\\max_m L_{i,m} L_{m,j}$", 8.2)

    arrow(23.5, 48, 23.5, 44.0)
    arrow(23.5, 35.5, 23.5, 31.5)
    arrow(23.5, 23, 23.5, 20.5)

    # ---------------- Receiver column (right) ----------------
    ax.text(78.5, 59.5, "Receiver Node $N_j$", fontsize=11, fontweight="bold", ha="center", color=C_TXT)

    box(56.5, 48, 42, 8.5, C_R1, edge=C_GREEN)
    label(78.5, 53.9, "Zero-Ground-Truth Structural Validation", 9.2, "bold", "#28502f")
    label(78.5, 50.1, "schema keys, pos length, $0\\leq bat\\leq 100$, freshness $ts\\leq t_{now}+1$", 7.8, color="#28502f")

    box(56.5, 35.5, 42, 8.5, C_R2, edge=C_GREEN)
    label(78.5, 41.9, "Decision Oracle + DPR", 9.5, "bold", "#28502f")
    label(78.5, 38.1, "$D=(\\mathrm{Quadrant},\\, \\mathrm{PriorityTier},\\, \\mathrm{EnergyAction})$;  DPR = agreement vs. raw $S_i$", 7.8, color="#28502f")

    box(56.5, 23, 42, 8.5, "#e0e0e0", edge="#666666")
    label(78.5, 29.4, "Commit & Forward (TTL = 3)", 9.5, "bold", "#333333")
    label(78.5, 25.6, "state_matrix[id] = token;  forward to neighbors except origin", 7.8, color="#333333")

    arrow(78.5, 48, 78.5, 44.0, C_GREEN)
    arrow(78.5, 35.5, 78.5, 31.5, C_GREEN)

    # ---------------- Channel band (bottom) ----------------
    box(14, 1.5, 72, 7.0, "#fff2cc", edge="#8a6d1a")
    label(50, 6.3, "DDIL Wireless Channel — Gilbert-Elliott Burst Loss (byte-scaled)", 9.5, "bold", "#5c4810")
    label(50, 3.3, "$P_{drop} = D_{env} \\cdot B_{payload}/B_{raw}$:   raw ~200 B faces full $D_{env}$;   ~104 B token faces $0.52\\,D_{env}$ (1.9x dilution)",
          8.0, color="#5c4810")

    arrow(23.5, 12.5, 23.5, 8.5, "#8a6d1a", 2.0)          # sender -> channel
    arrow(78.5, 8.5, 78.5, 23.0, C_GREEN, 2.0)             # channel -> receiver

    fig.tight_layout()
    path = f"{OUT}\\fig1_architecture.png"
    fig.savefig(path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[OK] {path}")


if __name__ == "__main__":
    figure1_architecture()
    print("Done.")
