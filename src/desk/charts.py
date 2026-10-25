"""Headline chart for the RTFO monitor: `python -m desk.charts` writes docs/rtfo_monitor.png."""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from desk.io import REPO_ROOT
from desk.monitor import OUTPUT_PATH, REFUELS_CSV

CHART_PATH = REPO_ROOT / "docs" / "rtfo_monitor.png"
SUPPLY_LABELS = {"observed_issuance": "Certificate issuance as\nobserved so far (Jan-May)", "flat": "Certificate supply flat\nvs 2025",
                 "volume_growth_upper_bound": "Upper bound: 2025 certificates\nper litre on 2026 volumes"}
DEMAND = {"formula": ("Obligation = formula", "#1b6ca8"), "calibrated_to_2024": ("Obligation x 2024 redeemed/formula ratio", "#d95f02")}


def build_chart(monitor: dict, prices: pd.DataFrame):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1.15, 1]})
    grid = pd.DataFrame(monitor["scenarios_2026"])
    grid["pct"] = grid["main_balance_pct_of_obligation"] * 100
    ypos = {k: i for i, k in enumerate(SUPPLY_LABELS)}
    for demand, (label, colour) in DEMAND.items():
        for j, (supply, y) in enumerate(ypos.items()):
            sel = grid[(grid.supply == supply) & (grid.demand == demand)].set_index("leftover_2024")["pct"]
            yy = y + (0.12 if demand == "formula" else -0.12)
            ax1.plot([sel["implied"], sel["published"]], [yy, yy], color=colour, lw=6, solid_capstyle="round", alpha=0.85,
                     label=label if j == 0 else None)
    ax1.axvline(0, color="#444", lw=1)
    ax1.set_yticks(list(ypos.values()), [SUPPLY_LABELS[k] for k in ypos])
    ax1.invert_yaxis()
    ax1.set_xlabel("2026 main-type RTFC balance, % of obligation (negative = short)")
    ax1.set_title("2026 main-type RTFCs: short on certificates issued so far", loc="left", fontsize=11)
    ax1.legend(loc="upper left", bbox_to_anchor=(0.0, -0.17), fontsize=8, frameon=False)
    ax1.text(0.0, -0.36, "Bar spans the unexplained 2024 gap: left end = 2024 leftover implied by flows, right end = as published by DfT.",
             transform=ax1.transAxes, fontsize=7.5, color="#555")

    p = prices.sort_values("month")
    ax2.plot(p["month"], p["avg_rtfc_pence"], marker="o", color="#1b6ca8")
    cap = monitor["price"]["buy_out_cap_pence"]
    ax2.axhline(cap, color="#c0392b", lw=1.2, ls="--")
    ax2.text(0, cap + 1, f"Buy-out cap {cap}p", color="#c0392b", fontsize=8)
    ax2.set_ylim(0, cap + 8)
    ax2.set_ylabel("pence per RTFC")
    ax2.set_title("ReFuels average RTFC price vs the cap", loc="left", fontsize=11)
    ax2.tick_params(axis="x", rotation=60, labelsize=8)
    ax2.text(0.0, -0.32, "One seller's average, method not stated; Dec 2025 and Sep 2026 not confirmed.", transform=ax2.transAxes,
             fontsize=7.5, color="#555")
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def main() -> None:
    monitor = json.loads(OUTPUT_PATH.read_text())
    CHART_PATH.parent.mkdir(parents=True, exist_ok=True)
    build_chart(monitor, pd.read_csv(REFUELS_CSV)).savefig(CHART_PATH, dpi=150, bbox_inches="tight")
    print(f"wrote {CHART_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
