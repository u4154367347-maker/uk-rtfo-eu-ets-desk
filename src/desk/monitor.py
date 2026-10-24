"""RTFO balance monitor: are main-obligation RTFCs long or short in 2026?

Built only from the committed tables in data/processed/ and config/rtfo_obligation.yaml.
Everything is in million certificates unless the name says otherwise. The model is a
transparent scenario grid, not a forecast: each assumption is a named input in the output JSON.

Chain of reasoning
1. Reconcile DfT's RF_0103 (issued - revoked - redeemed vs "still in existence").
2. Obligation = percentage x obligated amount. The obligated amount is approximated by total
   fossil fuel supplied (RF_0101). Check that against 2024, the last final year.
3. Close 2025: supply = 2025 net issuance + 2024 leftover (carry-over, capped at 25%).
4. 2026: supply scenarios from year-on-year growth in renewable fuel supplied so far.
5. Compare the latest RTFC price proxy with the 50p buy-out cap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from desk.io import PROCESSED_DIR, REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "rtfo_obligation.yaml"
OUTPUT_PATH = PROCESSED_DIR / "rtfo_monitor.json"
REFUELS_CSV = REPO_ROOT / "data" / "manual" / "refuels_rtfc_prices.csv"

MAIN_CATEGORIES = ["General", "Relevant Crop"]
DEV_CATEGORY = "Development Fuel"
IDENTITY_TOLERANCE = 0.05  # million certificates; DfT's own 2023 revoked total is off by 0.04


def _latest_release(df: pd.DataFrame) -> pd.DataFrame:
    """For each obligation period keep the most recent release (by data cut-off date) that contains it."""
    as_of = pd.to_datetime(df["data_as_of"]) if "data_as_of" in df else df["release"].rank(method="dense")
    best = as_of.groupby(df["obligation_period"]).transform("max")
    return df[as_of == best]


def _sum(balances: pd.DataFrame, period: int, status: str, categories: list[str]) -> float:
    b = _latest_release(balances)
    sel = b[(b.obligation_period == period) & (b.status == status) & (b.rtfc_category.isin(categories))]
    return float(sel["value"].sum())


def reconcile_balances(balances: pd.DataFrame) -> list[dict]:
    """One row per obligation period: does issued - revoked - redeemed equal what DfT says exists?"""
    rows = []
    for period, g in _latest_release(balances).groupby("obligation_period"):
        issued = _sum(balances, period, "Issued", ["Total Issued"])
        revoked = _sum(balances, period, "Revoked", ["Total Revoked"])
        redeemed = _sum(balances, period, "Redeemed", ["Total Redeemed"])
        existing = _sum(balances, period, "Still in existence", ["All categories"])
        implied = issued - revoked - redeemed
        recorded = redeemed > 0  # provisional files show no redemptions for the open periods
        rows.append({
            "obligation_period": int(period), "release": g["release"].iloc[0],
            "issued": issued, "revoked": revoked, "net_issued": issued - revoked, "redeemed": redeemed,
            "existing_published": existing, "existing_implied": implied, "gap": existing - implied,
            "redemptions_recorded": recorded,
            "identity_holds": bool(abs(existing - implied) <= IDENTITY_TOLERANCE) if recorded else None,
        })
    return rows


def net_issued(balances: pd.DataFrame, period: int) -> dict[str, float]:
    """Issued minus revoked, split into main-type (general + relevant crop) and development certificates."""
    def net(cats):
        return _sum(balances, period, "Issued", cats) - _sum(balances, period, "Revoked", cats)
    return {"main": net(MAIN_CATEGORIES), "development": net([DEV_CATEGORY])}


def _by_period(volumes: pd.DataFrame, fuel_type: str) -> pd.DataFrame:
    """Million litres by supply-period index (rows) and obligation year (columns), latest release per year."""
    v = _latest_release(volumes)
    v = v[v.fuel_type == fuel_type]
    return v.pivot_table(index="supply_period_index", columns="obligation_period", values="value", aggfunc="sum")


def last_complete_period(fossil: pd.Series) -> int:
    """Last supply period of a part-year file that looks complete.

    Provisional files list later periods as zero and the latest one part-filled, so a period
    counts as incomplete when it falls below 80% of the median of the earlier full periods.
    Index 0 (1-14 Jan) and 12 (December) are shorter by construction and never tested.
    """
    for i in range(2, 12):
        if fossil.loc[i] < 0.8 * fossil.loc[1:i - 1].median():
            return i - 1
    return 12


def obligated_amount(volumes: pd.DataFrame, year: int) -> float:
    """Total fossil fuel supplied in a full year, million litres equivalent (proxy for the obligated amount)."""
    return float(_by_period(volumes, "Total fossil fuel")[year].sum())


def ytd_growth(volumes: pd.DataFrame, year: int, base_year: int) -> dict:
    """Growth of fossil and renewable fuel supplied, over the supply periods complete in `year`."""
    fossil = _by_period(volumes, "Total fossil fuel")
    renew = _by_period(volumes, "Total renewable fuel")
    last = last_complete_period(fossil[year])
    idx = fossil.index[fossil.index <= last]
    return {
        "periods_compared": f"0..{last}",
        "fossil": float(fossil.loc[idx, year].sum() / fossil.loc[idx, base_year].sum() - 1),
        "renewable": float(renew.loc[idx, year].sum() / renew.loc[idx, base_year].sum() - 1),
    }


def issuance_growth(certificates: pd.DataFrame, year: int, base_year: int, max_period: int, floor: float = 0.7) -> dict:
    """Year-on-year change in main-type certificates actually issued, over the supply periods that look settled.

    Certificates are issued some time after fuel is supplied, so the latest periods of a provisional file
    are incomplete. A period counts as settled while its issuance is at least `floor` of the same period a
    year earlier; the first period below that (or `max_period`, whichever comes first) ends the window.
    """
    c = _latest_release(certificates)
    c = c[(c.measure == "rtfcs_issued") & ~c.is_total & c.rtfc_category.isin(MAIN_CATEGORIES)]
    by = c.pivot_table(index="supply_period_index", columns="obligation_period", values="value", aggfunc="sum")
    last = max_period
    for i in range(1, max_period + 1):
        if by.loc[i, year] < floor * by.loc[i, base_year]:
            last = i - 1
            break
    idx = by.index[by.index <= last]
    return {"periods_compared": f"0..{last}", "growth": float(by.loc[idx, year].sum() / by.loc[idx, base_year].sum() - 1)}


def calibrate_2024(balances: pd.DataFrame, volumes: pd.DataFrame, cfg: dict) -> dict:
    """Ratio of main-type certificates redeemed in 2024 to the formula obligation.

    The cause of a ratio below 1 is not known from public data: buy-out, the definition of the obligated
    amount (low sulphur gas oil is 5% of the proxy), or carry-over timing. It is an empirical scaling, not a
    measured demand ratio.
    """
    pct = cfg["obligation_pct"][2024]["main"] / 100
    formula = pct * obligated_amount(volumes, 2024)
    redeemed = _sum(balances, 2024, "Redeemed", MAIN_CATEGORIES)
    lsgo = float(_by_period(volumes, "Low sulphur gas oil")[2024].sum())
    return {"main_obligation_formula": formula, "main_type_redeemed": redeemed, "redeemed_over_formula": redeemed / formula,
            "low_sulphur_gas_oil_in_proxy": lsgo,
            "redeemed_over_formula_excluding_lsgo": redeemed / (pct * (obligated_amount(volumes, 2024) - lsgo))}


def close_2025(balances: pd.DataFrame, volumes: pd.DataFrame, cfg: dict, carry_in: float, demand_factor: float = 1.0) -> dict:
    """Main-type position for the 2025 obligation, using `carry_in` leftover certificates from 2024.

    `demand_factor` scales the formula obligation; use the same factor as in the 2026 scenario.
    """
    f25 = obligated_amount(volumes, 2025)
    obligation = cfg["obligation_pct"][2025]["main"] / 100 * f25 * demand_factor
    net = net_issued(balances, 2025)["main"]
    carry_used = min(carry_in, cfg["carry_over_max_share"] * obligation)
    balance = net + carry_used - obligation
    # Only 2025-issued certificates can be carried into 2026; 2024 ones expire after the 2025 obligation.
    leftover_2025 = min(max(balance, 0.0), net)
    return {"obligated_amount": f25, "obligation": obligation, "net_issued_main": net, "carry_in_used": carry_used,
            "balance": balance, "leftover_for_2026": leftover_2025}


def scenario_2026(balances, volumes, cfg, leftover_2025: float, supply_growth: float, demand_factor: float) -> dict:
    """Balance for 2026: 2025 main-type issuance grown by `supply_growth`, formula demand scaled by `demand_factor`.

    Development certificates are held at the 2025 level: year-to-date development volumes are too lumpy to scale.
    """
    f26 = obligated_amount(volumes, 2025) * (1 + ytd_growth(volumes, 2026, 2025)["fossil"])
    pct = cfg["obligation_pct"][2026]
    obligation = pct["main"] / 100 * f26 * demand_factor
    dev_target = pct["development"] / 100 * f26
    base = net_issued(balances, 2025)
    supply = base["main"] * (1 + supply_growth)
    carry_in = min(leftover_2025, cfg["carry_over_max_share"] * obligation)
    balance = supply + carry_in - obligation
    dev_supply = base["development"]
    dev_short = max(0.0, dev_target - dev_supply)
    return {
        "main_supply_growth": supply_growth, "demand_factor": demand_factor, "obligated_amount": f26,
        "main_obligation": obligation, "main_supply": supply, "carry_in": carry_in, "main_balance": balance,
        "main_balance_pct_of_obligation": balance / obligation,
        "main_buy_out_cost_gbp_m": max(0.0, -balance) * cfg["buy_out_pence"]["main"] / 100,
        "development_target": dev_target, "development_supply": dev_supply, "development_shortfall": dev_short,
        "development_buy_out_cost_gbp_m": dev_short * cfg["buy_out_pence"]["development"] / 100,
    }


def price_view(cfg: dict, csv_path: Path = REFUELS_CSV) -> dict:
    last = pd.read_csv(csv_path).sort_values("month").iloc[-1]
    cap = cfg["buy_out_pence"]["main"]
    prior = last["same_month_prior_year_pence"]
    return {"latest_month": last["month"], "avg_rtfc_pence": float(last["avg_rtfc_pence"]), "buy_out_cap_pence": cap,
            "same_month_prior_year_pence": None if pd.isna(prior) else float(prior),
            "share_of_cap": float(last["avg_rtfc_pence"]) / cap, "source_url": last["source_url"],
            "caveats": ["one seller's average, not a market index", "method not stated", "main-obligation RTFCs only",
                        "ReFuels says 2026 volume was sold forward at about 25-26p"]}


def build_monitor(processed: Path = PROCESSED_DIR, cfg_path: Path = CONFIG_PATH, csv_path: Path = REFUELS_CSV) -> dict:
    cfg = yaml.safe_load(cfg_path.read_text())
    balances = pd.read_parquet(processed / "rtfo_balances.parquet")
    volumes = pd.read_parquet(processed / "rtfo_volumes.parquet")
    certificates = pd.read_parquet(processed / "rtfo_certificates_issued.parquet")

    recon = reconcile_balances(balances)
    r24 = next(r for r in recon if r["obligation_period"] == 2024)
    dev_left = max(0.0, net_issued(balances, 2024)["development"] - _sum(balances, 2024, "Redeemed", [DEV_CATEGORY]))
    # The unexplained 2024 gap decides how many main-type certificates can carry into the 2025 obligation.
    leftover_choices = {"published": r24["existing_published"] - dev_left, "implied": r24["existing_implied"] - dev_left}

    calibration = calibrate_2024(balances, volumes, cfg)
    growth = ytd_growth(volumes, 2026, 2025)
    last_volume_period = int(growth["periods_compared"].split("..")[1])
    issuance = issuance_growth(certificates, 2026, 2025, max_period=last_volume_period)
    supply_cases = {
        "observed_issuance": issuance["growth"],            # what certificates show so far
        "flat": 0.0,                                        # 2025 level
        "volume_growth_upper_bound": max(0.0, growth["renewable"]),  # certificates per litre as in 2025 (contradicted so far)
    }
    demand_cases = {"formula": 1.0, "calibrated_to_2024": calibration["redeemed_over_formula"]}

    closes, grid = {}, []
    for dm_name, dm in demand_cases.items():
        for name, carry in leftover_choices.items():
            close = close_2025(balances, volumes, cfg, carry, dm)
            closes[f"{name}/{dm_name}"] = close
            for sg_name, sg in supply_cases.items():
                s = scenario_2026(balances, volumes, cfg, close["leftover_for_2026"], sg, dm)
                grid.append({"leftover_2024": name, "supply": sg_name, "demand": dm_name, **s})

    return {
        "units": "million certificates unless stated; money in GBP million",
        "inputs": {"config": "config/rtfo_obligation.yaml", "balances_releases": sorted(balances["release"].unique()),
                   "ytd_growth_2026_vs_2025": growth,
                   "issuance_growth_2026_vs_2025": issuance, "obligated_amount_proxy": "RF_0101 'Total fossil fuel' (million litres eq.)"},
        "reconciliation": recon,
        "calibration_2024": calibration,
        "leftover_2024_main_type": leftover_choices,
        "close_2025": closes,
        "scenarios_2026": grid,
        "price": price_view(cfg, csv_path),
    }


def main() -> None:
    result = build_monitor()
    OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} ({len(result['scenarios_2026'])} scenarios)")


if __name__ == "__main__":
    main()
