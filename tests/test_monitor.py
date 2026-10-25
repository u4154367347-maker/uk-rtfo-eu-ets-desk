"""Offline tests for the RTFO monitor: synthetic tables for the logic, committed tables for the regression."""

import json

import pandas as pd
import pytest

from desk import monitor

CFG = {
    "obligation_pct": {2024: {"main": 10.0, "development": 1.0}, 2025: {"main": 10.0, "development": 1.0},
                       2026: {"main": 10.0, "development": 1.0}},
    "buy_out_pence": {"main": 50, "development": 80},
    "carry_over_max_share": 0.25,
}


def _balances(rows):
    cols = ["release", "obligation_period", "status", "rtfc_category", "value"]
    return pd.DataFrame([("r1", *r) for r in rows], columns=cols)


def _volumes(fossil_by_year: dict[int, list[float]]):
    rows = []
    for year, values in fossil_by_year.items():
        for i, v in enumerate(values):
            rows.append(("r1", year, "Total fossil fuel", i, v))
            rows.append(("r1", year, "Total renewable fuel", i, v / 10))
            rows.append(("r1", year, "Total development renewable fuel", i, v / 100))
    return pd.DataFrame(rows, columns=["release", "obligation_period", "fuel_type", "supply_period_index", "value"])


def test_reconcile_flags_a_period_whose_identity_breaks():
    b = _balances([
        (2022, "Issued", "Total Issued", 100.0), (2022, "Revoked", "Total Revoked", 1.0),
        (2022, "Redeemed", "Total Redeemed", 99.0), (2022, "Still in existence", "All categories", 0.0),
        (2023, "Issued", "Total Issued", 100.0), (2023, "Revoked", "Total Revoked", 1.0),
        (2023, "Redeemed", "Total Redeemed", 50.0), (2023, "Still in existence", "All categories", 60.0),
    ])
    rows = {r["obligation_period"]: r for r in monitor.reconcile_balances(b)}
    assert rows[2022]["identity_holds"] is True
    assert rows[2023]["identity_holds"] is False
    assert rows[2023]["gap"] == pytest.approx(11.0)  # published 60 vs implied 49


def test_reconcile_does_not_judge_periods_without_redemptions():
    b = _balances([(2026, "Issued", "Total Issued", 10.0), (2026, "Revoked", "Total Revoked", 1.0),
                   (2026, "Redeemed", "Total Redeemed", 0.0), (2026, "Still in existence", "All categories", 9.0)])
    assert monitor.reconcile_balances(b)[0]["identity_holds"] is None


def test_latest_release_wins_per_period():
    df = pd.DataFrame({"release": ["2024-final", "2025-fifth", "2025-fifth"], "obligation_period": [2024, 2024, 2025],
                       "value": [1, 2, 3]})
    assert sorted(monitor._latest_release(df)["value"]) == [2, 3]


def test_last_complete_period_stops_at_the_part_filled_period():
    fossil = pd.Series([40, 100, 100, 100, 100, 100, 100, 55, 0, 0, 0, 0, 0], index=range(13), dtype=float)
    assert monitor.last_complete_period(fossil) == 6


def test_last_complete_period_ignores_short_first_and_last_periods():
    fossil = pd.Series([40] + [100] * 11 + [50], index=range(13), dtype=float)
    assert monitor.last_complete_period(fossil) == 12


def test_close_2025_caps_carry_over_at_a_quarter_of_the_obligation():
    b = _balances([(2025, "Issued", "General", 90.0), (2025, "Revoked", "General", 0.0)])
    v = _volumes({2025: [100.0] * 13})  # obligated amount 1300, obligation 130, cap 32.5
    close = monitor.close_2025(b, v, CFG, carry_in=500.0)
    assert close["carry_in_used"] == pytest.approx(32.5)
    assert close["balance"] == pytest.approx(90 + 32.5 - 130)
    assert close["leftover_for_2026"] == 0.0


def test_close_2025_leftover_never_exceeds_2025_issuance():
    b = _balances([(2025, "Issued", "General", 200.0), (2025, "Revoked", "General", 0.0)])
    v = _volumes({2025: [100.0] * 13})
    close = monitor.close_2025(b, v, CFG, carry_in=30.0)
    assert close["leftover_for_2026"] == pytest.approx(100.0)  # 200 + 30 - 130


def test_scenario_buy_out_cost_uses_the_cap_prices():
    b = _balances([(2025, "Issued", "General", 100.0), (2025, "Revoked", "General", 0.0),
                   (2025, "Issued", "Development Fuel", 10.0), (2025, "Revoked", "Development Fuel", 0.0)])
    v = _volumes({2025: [100.0] * 13, 2026: [100.0] * 7 + [0.0] * 6})
    s = monitor.scenario_2026(b, v, CFG, leftover_2025=0.0, supply_growth=0.0, demand_factor=1.0)
    assert s["main_obligation"] == pytest.approx(130.0)
    assert s["main_balance"] == pytest.approx(-30.0)
    assert s["main_buy_out_cost_gbp_m"] == pytest.approx(15.0)  # 30 certificates x 50p
    assert s["development_shortfall"] == pytest.approx(13.0 - 10.0)
    assert s["development_buy_out_cost_gbp_m"] == pytest.approx(3 * 0.8)


def test_close_2025_demand_factor_scales_the_obligation_like_in_2026():
    b = _balances([(2025, "Issued", "General", 90.0), (2025, "Revoked", "General", 0.0)])
    v = _volumes({2025: [100.0] * 13})
    plain = monitor.close_2025(b, v, CFG, carry_in=10.0)
    scaled = monitor.close_2025(b, v, CFG, carry_in=10.0, demand_factor=0.5)
    assert scaled["obligation"] == pytest.approx(plain["obligation"] * 0.5)
    assert scaled["balance"] == pytest.approx(90 + 10 - 65)


def test_scenario_demand_factor_scales_the_main_obligation_only():
    b = _balances([(2025, "Issued", "General", 100.0), (2025, "Revoked", "General", 0.0)])
    v = _volumes({2025: [100.0] * 13, 2026: [100.0] * 7 + [0.0] * 6})
    full = monitor.scenario_2026(b, v, CFG, 0.0, 0.0, 1.0)
    half = monitor.scenario_2026(b, v, CFG, 0.0, 0.0, 0.5)
    assert half["main_obligation"] == pytest.approx(full["main_obligation"] / 2)
    assert half["development_target"] == pytest.approx(full["development_target"])


def _issued(by_year: dict[int, list[float]]):
    rows = [("r1", "2026-09-03", year, "rtfcs_issued", "General", i, v, False)
            for year, values in by_year.items() for i, v in enumerate(values)]
    return pd.DataFrame(rows, columns=["release", "data_as_of", "obligation_period", "measure", "rtfc_category",
                                       "supply_period_index", "value", "is_total"])


def test_issuance_growth_stops_at_the_first_period_far_below_last_year():
    c = _issued({2025: [10.0] * 13, 2026: [8.0, 8.0, 8.0, 4.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]})
    g = monitor.issuance_growth(c, 2026, 2025, max_period=6)
    assert g["periods_compared"] == "0..2"
    assert g["growth"] == pytest.approx(-0.2)


def test_issuance_growth_respects_max_period():
    c = _issued({2025: [10.0] * 13, 2026: [11.0] * 13})
    assert monitor.issuance_growth(c, 2026, 2025, max_period=4)["periods_compared"] == "0..4"


def test_committed_monitor_json_is_not_stale():
    assert monitor.build_monitor() == json.loads(monitor.OUTPUT_PATH.read_text())


def test_real_data_pinned_values():
    """Hand-checked against the source tables, so a logic change cannot be hidden by regenerating the JSON."""
    m = json.loads(monitor.OUTPUT_PATH.read_text())
    assert m["close_2025"]["published/formula"]["obligated_amount"] == pytest.approx(45512.2, abs=0.1)
    assert m["close_2025"]["published/formula"]["net_issued_main"] == pytest.approx(5402.3, abs=0.1)
    assert m["calibration_2024"]["redeemed_over_formula"] == pytest.approx(0.908, abs=0.001)
    assert m["calibration_2024"]["redeemed_over_formula_excluding_lsgo"] == pytest.approx(0.957, abs=0.001)
    assert m["inputs"]["issuance_growth_2026_vs_2025"]["periods_compared"] == "0..3"
    assert m["inputs"]["issuance_growth_2026_vs_2025"]["growth"] == pytest.approx(-0.158, abs=0.001)
    assert len(m["scenarios_2026"]) == 12


def test_calibrated_cases_use_the_calibrated_factor_in_both_years():
    m = json.loads(monitor.OUTPUT_PATH.read_text())
    factor = m["calibration_2024"]["redeemed_over_formula"]
    f = m["close_2025"]
    assert f["published/calibrated_to_2024"]["obligation"] == pytest.approx(f["published/formula"]["obligation"] * factor)
    for s in m["scenarios_2026"]:
        if s["demand"] == "calibrated_to_2024":
            assert s["demand_factor"] == pytest.approx(factor)


def test_real_data_reconciliation_facts():
    rows = {r["obligation_period"]: r for r in json.loads(monitor.OUTPUT_PATH.read_text())["reconciliation"]}
    assert rows[2021]["identity_holds"] and rows[2022]["identity_holds"]
    assert rows[2023]["identity_holds"] is False and rows[2024]["identity_holds"] is False
    assert rows[2024]["gap"] == pytest.approx(328.0, abs=0.1)
