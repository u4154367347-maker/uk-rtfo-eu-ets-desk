"""RTFO balance monitor: are main-obligation RTFCs long or short in 2026?

Built only from the committed tables in data/processed/ and config/rtfo_obligation.yaml.
Everything is in million certificates unless the name says otherwise. The model is a
transparent scenario grid, not a forecast: each assumption is a named input in the output JSON.

Chain of reasoning
1. Reconcile DfT's RF_0103 (issued - revoked - redeemed vs "still in existence").
2. Obligation = percentage x obligated amount. The obligated amount is approximated by total
   fossil fuel supplied (RF_0101). Check that against 2024, the last final year.
3. Close 2025: supply = 2025 net issuance (scaled for fuel not yet certificated) + 2024 leftover
   (carry-over, capped at 25%).
4. 2026: supply scenarios from year-on-year issuance growth, adjusted for the share of fuel not yet
   certificated (RF_0102), and from growth in renewable fuel supplied so far.
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


def certificated_share(certificates: pd.DataFrame) -> pd.DataFrame:
    """Share of renewable fuel supplied that has had RTFCs issued, by supply period (rows) and year (columns).

    DfT's RF_0102 publishes this column. In a provisional file it is well below 1 for recent periods because
    certificates are claimed some time after the fuel is supplied; in a final file it is close to 1.
    """
    c = _latest_release(certificates)
    def total(measure):
        sel = c[(c.measure == measure) & (c.rtfc_category == "Total")]
        return sel.pivot_table(index="supply_period_index", columns="obligation_period", values="value", aggfunc="sum")
    return total("volume_with_rtfcs") / total("renewable_volume_total")


def issuance_growth(certificates: pd.DataFrame, year: int, base_year: int, max_period: int, min_share: float = 0.5,
                    max_share_gap: float = 0.05) -> dict:
    """Year-on-year change in main-type certificates issued, raw and adjusted for issuance lag.

    Raw issuance in a young provisional file is low only because part of the fuel has not been certificated
    yet. The adjusted figure divides each period's issuance by the share of that period's renewable fuel
    that has certificates (`certificated_share`), i.e. it assumes the fuel still to be certificated earns
    certificates at the same rate per litre as the fuel already certificated. The window runs from period 0
    while that share is at least `min_share` in both years, up to `max_period`. The raw figure is comparable
    only if the two years' shares differ by at most `max_share_gap`.
    """
    c = _latest_release(certificates)
    c = c[(c.measure == "rtfcs_issued") & ~c.is_total & c.rtfc_category.isin(MAIN_CATEGORIES)]
    by = c.pivot_table(index="supply_period_index", columns="obligation_period", values="value", aggfunc="sum")
    share = certificated_share(certificates)
    last = max_period
    for i in range(0, max_period + 1):
        if min(share.loc[i, year], share.loc[i, base_year]) < min_share:
            last = i - 1
            break
    if last < 0:
        raise ValueError("no supply period is certificated enough to compare")
    idx = by.index[by.index <= last]

    def ratio(a, b):
        return float(a.sum() / b.sum() - 1)

    share_year, share_base = (float(share.loc[idx, y].mean()) for y in (year, base_year))  # unweighted mean over the window
    return {
        "periods_compared": f"0..{last}",
        "raw_growth": ratio(by.loc[idx, year], by.loc[idx, base_year]),
        "lag_adjusted_growth": ratio(by.loc[idx, year] / share.loc[idx, year], by.loc[idx, base_year] / share.loc[idx, base_year]),
        "certificated_share": {str(year): share_year, str(base_year): share_base},
        "raw_comparable": bool(abs(share_year - share_base) <= max_share_gap),
    }


def completeness_factor(certificates: pd.DataFrame, year: int, final_year: int) -> float:
    """How much `year`'s issuance would rise if it ended as completely certificated as `final_year` did.

    Ratio of the full-year certificated shares (volume with RTFCs / renewable volume): final_year / year.
    """
    c = _latest_release(certificates)
    def total(measure, y):
        sel = c[(c.measure == measure) & (c.rtfc_category == "Total") & (c.obligation_period == y)]
        return float(sel["value"].sum())
    def share(y):
        return total("volume_with_rtfcs", y) / total("renewable_volume_total", y)
    return share(final_year) / share(year)


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


def close_2025(balances: pd.DataFrame, volumes: pd.DataFrame, cfg: dict, carry_in: float, demand_factor: float = 1.0,
               base_scale: float = 1.0) -> dict:
    """Main-type position for the 2025 obligation, using `carry_in` leftover certificates from 2024.

    `demand_factor` scales the formula obligation; use the same factor as in the 2026 scenario.
    `base_scale` scales 2025 net main-type issuance for fuel not yet certificated (see `completeness_factor`).
    """
    f25 = obligated_amount(volumes, 2025)
    obligation = cfg["obligation_pct"][2025]["main"] / 100 * f25 * demand_factor
    net = net_issued(balances, 2025)["main"] * base_scale
    carry_used = min(carry_in, cfg["carry_over_max_share"] * obligation)
    balance = net + carry_used - obligation
    # Only 2025-issued certificates can be carried into 2026; 2024 ones expire after the 2025 obligation.
    leftover_2025 = min(max(balance, 0.0), net)
    return {"obligated_amount": f25, "obligation": obligation, "net_issued_main": net, "carry_in_used": carry_used,
            "balance": balance, "leftover_for_2026": leftover_2025}


def scenario_2026(balances, volumes, cfg, leftover_2025: float, supply_growth: float, demand_factor: float,
                  base_scale: float = 1.0) -> dict:
    """Balance for 2026: 2025 main-type issuance (x `base_scale`) grown by `supply_growth`, formula demand x `demand_factor`.

    Development certificates are held at the published 2025 level (not scaled): year-to-date development
    volumes are too lumpy to scale, so the development shortfall is an upper estimate.
    """
    f26 = obligated_amount(volumes, 2025) * (1 + ytd_growth(volumes, 2026, 2025)["fossil"])
    pct = cfg["obligation_pct"][2026]
    obligation = pct["main"] / 100 * f26 * demand_factor
    dev_target = pct["development"] / 100 * f26
    base = net_issued(balances, 2025)
    supply = base["main"] * base_scale * (1 + supply_growth)
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
    # 2025 is still provisional: scale its main-type issuance to the completeness 2024 reached in its final release.
    base_scale = completeness_factor(certificates, 2025, 2024)
    supply_cases = {
        "issuance_lag_adjusted": issuance["lag_adjusted_growth"],  # certificates per certificated litre, on all 2026 fuel so far
        "renewable_volume_growth": growth["renewable"],             # 2025 certificates per litre on 2026 volumes
        "flat": 0.0,                                                # downside: no growth on (adjusted) 2025
    }
    demand_cases = {"formula": 1.0, "calibrated_to_2024": calibration["redeemed_over_formula"]}

    closes, grid = {}, []
    for dm_name, dm in demand_cases.items():
        for name, carry in leftover_choices.items():
            close = close_2025(balances, volumes, cfg, carry, dm, base_scale)
            closes[f"{name}/{dm_name}"] = close
            for sg_name, sg in supply_cases.items():
                s = scenario_2026(balances, volumes, cfg, close["leftover_for_2026"], sg, dm, base_scale)
                grid.append({"leftover_2024": name, "supply": sg_name, "demand": dm_name, **s})

    return {
        "units": "million certificates unless stated; money in GBP million",
        "inputs": {"config": "config/rtfo_obligation.yaml", "balances_releases": sorted(balances["release"].unique()),
                   "ytd_growth_2026_vs_2025": growth,
                   "issuance_growth_2026_vs_2025": issuance, "completeness_factor_2025": base_scale,
                   "net_issued_main_2025_published": net_issued(balances, 2025)["main"], "obligated_amount_proxy": "RF_0101 'Total fossil fuel' (million litres eq.)"},
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
