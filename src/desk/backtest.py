"""EUA auction backtest: does strong demand at one auction say anything about later auction prices?

Protocol: config/backtest.yaml, committed before any result was computed. This module implements it
as written. The only price in the data is the auction clearing price, so "returns" are changes
between consecutive EU common-platform auctions, not secondary-market returns.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from desk.io import PROCESSED_DIR, REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "backtest.yaml"
OUTPUT_PATH = PROCESSED_DIR / "eua_backtest.json"
AUCTIONS_PATH = PROCESSED_DIR / "eex_auctions.parquet"


def load_series(auctions: pd.DataFrame) -> pd.DataFrame:
    """EU common-platform EUA auctions with a price, one row per date, oldest first."""
    a = auctions[(auctions["country"] == "EU") & auctions["is_eua"] & auctions["price_eur"].notna()]
    a = a.sort_values("date")
    if a["date"].duplicated().any():
        raise ValueError("more than one EU EUA auction on the same date; the series is ambiguous")
    return a[["date", "price_eur", "cover_ratio", "volume_t"]].reset_index(drop=True)


def add_signal(series: pd.DataFrame, lookback: int = 60, threshold: float = 1.0) -> pd.DataFrame:
    """Relative cover ratio = this auction's cover ratio over the median of the previous `lookback` auctions."""
    s = series.copy()
    prior_median = s["cover_ratio"].rolling(lookback).median().shift(1)  # shift: the current auction is excluded
    s["relative_cover"] = s["cover_ratio"] / prior_median
    s["strong"] = (s["relative_cover"] > threshold).astype(float).where(s["relative_cover"].notna())
    s["step_return"] = np.log(s["price_eur"].shift(-1) / s["price_eur"])  # auction j -> auction j+1
    return s


def positions(s: pd.DataFrame, kind: str, timing: str) -> pd.Series:
    """Position held over the step j -> j+1.

    primary: decided by the signal of auction j-1 (known after that auction, entered at auction j).
    diagnostic: decided by the signal of auction j itself (enters at P[j]; optimistic).
    """
    sig = s["strong"].shift(1) if timing == "primary" else s["strong"]
    return sig if kind == "long_flat" else (2 * sig - 1)


def run(step_returns: np.ndarray, pos: np.ndarray, cost_bps: float) -> np.ndarray:
    """Net log return per step: position x return, minus cost per unit of position change (starting flat)."""
    changes = np.abs(np.diff(pos, prepend=0.0))
    return pos * step_returns - cost_bps / 1e4 * changes


def max_drawdown(net: np.ndarray) -> float:
    equity = np.exp(np.cumsum(net))
    return float((equity / np.maximum.accumulate(equity) - 1).min())


def metrics(net: np.ndarray, pos: np.ndarray, gross: np.ndarray, years: float) -> dict:
    per_year = len(net) / years
    invested = pos != 0
    return {
        "steps": int(len(net)), "mean_per_auction": float(net.mean()), "total_log_return": float(net.sum()),
        "annualised_sharpe": float(net.mean() / net.std(ddof=1) * np.sqrt(per_year)) if net.std() > 0 else None,
        "max_drawdown": max_drawdown(net), "exposure": float(invested.mean()),
        "trades": float(np.abs(np.diff(pos, prepend=0.0)).sum()),
        "hit_rate_when_invested": float((np.sign(pos[invested]) * gross[invested] > 0).mean()) if invested.any() else None,
    }


def permutation_p(step_returns: np.ndarray, pos: np.ndarray, cost_bps: float, draws: int, seed: int) -> float:
    """One-sided p: share of random re-orderings of the same positions that do at least as well in total net return."""
    rng = np.random.default_rng(seed)
    observed = run(step_returns, pos, cost_bps).sum()
    better = sum(run(step_returns, rng.permutation(pos), cost_bps).sum() >= observed for _ in range(draws))
    return float((1 + better) / (1 + draws))


def evaluate(s: pd.DataFrame, start: str, end: str | None, cfg: dict) -> dict:
    """All pre-registered variants over steps whose entry auction lies in [start, end]."""
    window = s[(s["date"] >= start) & (s["date"] <= (end or s["date"].max()))]
    window = window[window["step_return"].notna()]
    out = {"first_entry": str(window["date"].min().date()), "last_entry": str(window["date"].max().date()), "steps": len(window)}
    years = (window["date"].max() - window["date"].min()).days / 365.25
    ret = window["step_return"].to_numpy()
    grid, head = cfg["costs"]["grid_bps"], cfg["costs"]["headline_bps"]
    perm = cfg["benchmarks"]["shuffled_signal"]

    results = {}
    for timing in ("primary", "diagnostic"):
        for kind in ("long_flat", "long_short"):
            pos = positions(s, kind, timing).loc[window.index]
            if pos.isna().any():
                raise ValueError("signal missing inside the evaluation window; the lookback has not filled")
            pos = pos.to_numpy()
            by_cost = {}
            for c in grid:
                net = run(ret, pos, c)
                by_cost[str(c)] = metrics(net, pos, ret, years)
            by_cost[str(head)]["permutation_p_one_sided"] = permutation_p(ret, pos, head, perm["draws"], perm["seed"])
            invested = pos > 0
            results[f"{timing}/{kind}"] = {
                "by_cost_bps": by_cost,
                "mean_return_when_strong": float(ret[invested].mean()) if invested.any() else None,
                "mean_return_when_not_strong": float(ret[~invested].mean()) if (~invested).any() else None,
            }
    long_pos = np.ones(len(ret))
    results["always_long"] = {"by_cost_bps": {str(c): metrics(run(ret, long_pos, c), long_pos, ret, years) for c in grid}}
    out["results"] = results
    out["years"] = years
    return out


def verdict(oos: dict, head: int) -> dict:
    """Apply the pre-registered decision rule to the primary long/flat variant at the headline cost."""
    strat = oos["results"]["primary/long_flat"]["by_cost_bps"][str(head)]
    bench = oos["results"]["always_long"]["by_cost_bps"][str(head)]
    checks = {
        "net_mean_above_always_long": strat["mean_per_auction"] > bench["mean_per_auction"],
        "permutation_p_below_0.05": strat["permutation_p_one_sided"] < 0.05,
        "drawdown_no_worse_than_always_long": strat["max_drawdown"] >= bench["max_drawdown"],
    }
    return {"checks": checks, "signal_useful": all(checks.values())}


def build_backtest(auctions_path: Path = AUCTIONS_PATH, cfg_path: Path = CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text())
    series = add_signal(load_series(pd.read_parquet(auctions_path)), cfg["signal"]["lookback_auctions"])
    ps = cfg["periods"]
    ins = evaluate(series, str(ps["in_sample"]["start"]), str(ps["in_sample"]["end"]), cfg)
    oos = evaluate(series, str(ps["out_of_sample"]["start"]), None, cfg)
    return {
        "protocol": "config/backtest.yaml (committed before results)",
        "data": {"auctions": len(series), "first": str(series["date"].min().date()), "last": str(series["date"].max().date()),
                 "largest_abs_step_return": float(series["step_return"].abs().max())},
        "in_sample": ins, "out_of_sample": oos,
        "verdict_out_of_sample": verdict(oos, cfg["costs"]["headline_bps"]),
    }


def main() -> None:
    result = build_backtest()
    OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}; verdict: {result['verdict_out_of_sample']}")


if __name__ == "__main__":
    main()
