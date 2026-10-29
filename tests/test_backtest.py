"""Offline tests for the EUA backtest: timing and cost logic on tiny hand-made series, plus pinned real-data facts."""

import json

import numpy as np
import pandas as pd
import pytest

from desk import backtest


def _auctions(prices, covers, country="EU"):
    n = len(prices)
    return pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n), "country": country, "is_eua": True,
                         "price_eur": prices, "cover_ratio": covers, "volume_t": 1.0})


def test_load_series_keeps_only_eu_eua_auctions_with_a_price():
    a = pd.concat([_auctions([10.0, 11.0], [1.0, 1.0]), _auctions([99.0, 99.0], [1.0, 1.0], country="DE")])
    a.loc[0, "price_eur"] = np.nan  # a cancelled auction
    a.loc[a["country"] == "DE", "date"] += pd.Timedelta(days=50)
    s = backtest.load_series(a)
    assert list(s["price_eur"]) == [11.0]


def test_load_series_refuses_two_auctions_on_one_date():
    a = pd.concat([_auctions([10.0], [1.0]), _auctions([11.0], [1.0])])
    with pytest.raises(ValueError, match="same date"):
        backtest.load_series(a)


def test_signal_excludes_the_current_auction_from_its_own_median():
    s = backtest.add_signal(backtest.load_series(_auctions([10.0] * 6, [1, 1, 1, 1, 1, 9])), lookback=5)
    assert s["relative_cover"].iloc[5] == pytest.approx(9.0)  # 9 / median(1,1,1,1,1), not 9 / median(1,1,1,1,9)
    assert s["strong"].iloc[5] == 1.0
    assert np.isnan(s["strong"].iloc[3])  # lookback not filled yet


def test_primary_position_uses_last_auctions_signal_diagnostic_uses_this_one():
    s = pd.DataFrame({"strong": [0.0, 1.0, 0.0, 1.0]})
    assert list(backtest.positions(s, "long_flat", "diagnostic")) == [0, 1, 0, 1]
    primary = backtest.positions(s, "long_flat", "primary")
    assert np.isnan(primary.iloc[0]) and list(primary.iloc[1:]) == [0, 1, 0]
    assert list(backtest.positions(s, "long_short", "diagnostic")) == [-1, 1, -1, 1]


def test_run_charges_every_position_change_including_the_first_entry():
    ret = np.array([0.01, 0.01, 0.01, 0.01])
    pos = np.array([1.0, 1.0, 0.0, 1.0])  # enter, hold, exit, re-enter = 3 changes
    net = backtest.run(ret, pos, cost_bps=10)
    assert net.sum() == pytest.approx(0.03 - 3 * 0.001)


def test_run_charges_a_flip_as_two_units():
    net = backtest.run(np.array([0.0, 0.0]), np.array([1.0, -1.0]), cost_bps=100)
    assert net.sum() == pytest.approx(-(1 + 2) * 0.01)


def test_max_drawdown_of_a_known_path():
    net = np.log(np.array([1.1, 0.5, 2.0]))  # equity 1.1, 0.55, 1.1 -> drawdown -50%
    assert backtest.max_drawdown(net) == pytest.approx(-0.5)


def test_permutation_p_is_small_for_a_perfect_signal_and_large_for_a_useless_one():
    rng = np.random.default_rng(1)
    ret = rng.normal(0, 0.01, 200)
    perfect = (ret > 0).astype(float)
    useless = rng.permutation(perfect)
    assert backtest.permutation_p(ret, perfect, 0, draws=500, seed=1) < 0.01
    assert backtest.permutation_p(ret, useless, 0, draws=500, seed=1) > 0.05


def test_evaluate_rejects_a_window_where_the_signal_is_not_ready():
    s = backtest.add_signal(backtest.load_series(_auctions(list(range(10, 30)), [1.0] * 20)), lookback=60)
    cfg = {"costs": {"grid_bps": [0], "headline_bps": 0}, "benchmarks": {"shuffled_signal": {"draws": 10, "seed": 1}}}
    with pytest.raises(ValueError, match="lookback"):
        backtest.evaluate(s, "2020-01-01", None, cfg)


def test_protocol_was_committed_with_the_pinned_values():
    cfg = backtest.yaml.safe_load(backtest.CONFIG_PATH.read_text())
    assert cfg["signal"]["lookback_auctions"] == 60 and cfg["costs"]["headline_bps"] == 10
    assert str(cfg["periods"]["out_of_sample"]["start"]) == "2023-01-01"


def _approx(x):
    """Same structure, floats compared to 1e-9 relative (last-digit differences across machines are not staleness)."""
    if isinstance(x, dict):
        return {k: _approx(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_approx(v) for v in x]
    return pytest.approx(x, rel=1e-9) if isinstance(x, float) else x


def test_committed_results_are_not_stale():
    fresh = json.loads(json.dumps(backtest.build_backtest()))
    assert fresh == _approx(json.loads(backtest.OUTPUT_PATH.read_text()))


def test_circular_shift_p_is_small_for_a_perfect_periodic_signal_and_a_valid_probability():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.01, 200)
    pos = (r > 0).astype(float)
    p = backtest.circular_shift_p(r, pos, 0.0)
    assert 0.0 <= p <= 0.05
    assert 0.0 <= backtest.circular_shift_p(r, rng.integers(0, 2, 200).astype(float), 10.0) <= 1.0


def test_spread_and_bootstrap_interval_bracket_a_known_effect():
    rng = np.random.default_rng(2)
    pos = rng.integers(0, 2, 400).astype(float)
    r = rng.normal(0, 0.005, 400) + 0.01 * pos
    assert backtest.spread(r, pos) == pytest.approx(0.01, abs=0.002)
    lo, hi = backtest.block_bootstrap_spread_ci(r, pos, 500, 7)
    assert lo < 0.01 < hi and lo > 0


def test_exploratory_block_is_present_and_marked_post_hoc():
    r = json.loads(backtest.OUTPUT_PATH.read_text())
    assert "exploratory_post_hoc" in r["out_of_sample"]
    assert 0 <= r["out_of_sample"]["results"]["primary/long_flat"]["by_cost_bps"]["10"]["circular_shift_p_one_sided"] <= 1


def test_real_data_pinned_values():
    r = json.loads(backtest.OUTPUT_PATH.read_text())
    assert r["data"]["auctions"] == 1948
    oos = r["out_of_sample"]
    assert oos["first_entry"] == "2023-01-09" and oos["steps"] == 547
    strat = oos["results"]["primary/long_flat"]["by_cost_bps"]["10"]
    assert strat["trades"] == 229 and strat["exposure"] == pytest.approx(0.45, abs=0.01)
    assert strat["permutation_p_one_sided"] > 0.05
    assert r["verdict_out_of_sample"]["signal_useful"] is False


def test_auctions_after_the_frozen_cutoff_do_not_change_the_results(tmp_path):
    raw = backtest.pd.read_parquet(backtest.AUCTIONS_PATH)
    extra = raw[raw["country"] == "EU"].tail(1).copy()
    extra["date"] = backtest.pd.Timestamp("2026-12-01")
    extra["price_eur"] = extra["price_eur"] * 3
    path = tmp_path / "auctions.parquet"
    backtest.pd.concat([raw, extra], ignore_index=True).to_parquet(path)
    assert backtest.build_backtest(path)["data"] == json.loads(backtest.OUTPUT_PATH.read_text())["data"]


def test_exploratory_fields_are_stored_and_the_shuffle_trade_count_exceeds_the_real_one():
    oos = json.loads(backtest.OUTPUT_PATH.read_text())["out_of_sample"]
    ex = oos["exploratory_post_hoc"]
    real_trades = oos["results"]["primary/long_flat"]["by_cost_bps"]["10"]["trades"]
    assert ex["primary"]["shuffle_mean_trades"] > real_trades
    assert set(ex["rank_corr_relative_cover_with_return"]) == {f"signal_{k}_auctions_before_the_step" for k in (0, 1, 2)}


def test_break_even_cost_matches_the_grid():
    oos = json.loads(backtest.OUTPUT_PATH.read_text())["out_of_sample"]
    be = oos["break_even_cost_bps_vs_always_long"]["primary/long_flat"]
    res = oos["results"]
    gap = lambda c: res["primary/long_flat"]["by_cost_bps"][c]["mean_per_auction"] - res["always_long"]["by_cost_bps"][c]["mean_per_auction"]
    assert gap("5") > 0 and gap("20") < 0 and 5 < be < 20  # the sign change lies between the grid points
    assert gap("10") * (be - 10) > 0  # on the side of 10bp that the break-even implies
