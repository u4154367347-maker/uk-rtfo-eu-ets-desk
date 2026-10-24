from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from desk import rtfo
from desk.io import RAW_DIR, load_sources

FIXTURE = Path(__file__).parent / "fixtures" / "rf01_sample.ods"


@pytest.fixture(scope="module")
def tables():
    return rtfo.parse_workbook(FIXTURE, release="fixture", release_status="provisional")


def test_header_and_metadata_are_found():
    sheet = rtfo.read_sheet(FIXTURE, "RF_0103")
    assert sheet.data_as_of == "2026-09-03"
    assert list(sheet.body.columns[:2]) == ["Status", "GHG saving category / RTFC category"]
    assert "Notes" not in sheet.body.columns


def test_every_table_has_common_columns(tables):
    assert set(tables) == {"volumes", "certificates_issued", "balances", "transfers", "obligation_performance"}
    for df in tables.values():
        assert list(df.columns[:4]) == ["release", "release_status", "data_as_of", "table_id"]
        assert "unit" in df.columns and df["unit"].notna().all()


def test_balances_values_match_the_published_sheet(tables):
    b = tables["balances"]
    assert sorted(b["obligation_period"].unique()) == [2023, 2024, 2025, 2026]
    issued_2024 = b.query("status == 'Issued' and rtfc_category == 'Total Issued' and obligation_period == 2024")
    assert issued_2024["value"].item() == pytest.approx(6682.210173)
    assert b.loc[b["obligation_period"] >= 2025, "period_is_provisional"].all()
    assert not b.loc[b["obligation_period"] <= 2024, "period_is_provisional"].any()


def test_volumes_are_numeric_and_cover_all_supply_periods(tables):
    v = tables["volumes"]
    assert v["value"].notna().all()
    assert set(v["supply_period"]) == set(rtfo.SUPPLY_PERIODS)
    assert (v["obligation_period"] == 2026).all()


def test_obligation_performance_shares_are_fractions(tables):
    p = tables["obligation_performance"]
    assert p["share"].between(0, 1).all()
    assert set(p["obligation"]) == {"main", "development"}
    assert set(p["route"]) == {"rtfcs", "buy_out"}


def test_transfers_category_prefix_is_removed(tables):
    assert set(tables["transfers"]["rtfc_category"]) == {"Development Fuel", "General", "Relevant Crop", "Total"}


def test_broken_total_raises(tmp_path):
    raw = pd.read_excel(FIXTURE, engine="odf", sheet_name=None, header=None, dtype=object)
    raw["RF_0103"].iat[13, 2] = 9999.0  # "Total Issued" for 2023
    broken = tmp_path / "broken.ods"
    with pd.ExcelWriter(broken, engine="odf") as writer:
        for name, df in raw.items():
            df.to_excel(writer, sheet_name=name, header=False, index=False)

    with pytest.raises(rtfo.TotalsMismatch, match="RF_0103 Issued 2023"):
        rtfo.parse_workbook(broken, "broken", "provisional")

    # The same mismatch passes once it is documented as known.
    sheet = rtfo.read_sheet(broken, "RF_0103")
    rtfo.parse_balances(sheet)
    rtfo.parse_workbook(broken, "broken", "provisional", known_mismatches=sheet.mismatches)


def test_unexpected_text_in_numeric_cell_raises():
    with pytest.raises(ValueError):
        rtfo._to_number("12,3")
    assert np.isnan(rtfo._to_number("[x]"))


@pytest.mark.sources
def test_all_configured_releases_parse():
    entries = load_sources()["rtfo"]["files"]
    paths = [RAW_DIR / "rtfo" / e["url"].rsplit("/", 1)[-1] for e in entries]
    if not all(p.exists() for p in paths):
        pytest.skip("source files not downloaded; run `make data`")
    for entry, path in zip(entries, paths):
        tables = rtfo.parse_workbook(path, entry["release"], entry["status"], entry.get("known_mismatches"))
        assert {"volumes", "certificates_issued", "balances", "transfers"} <= set(tables)
        assert all(len(df) > 0 for df in tables.values())
