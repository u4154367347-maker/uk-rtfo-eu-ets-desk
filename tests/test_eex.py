import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from desk import eex
from desk.io import RAW_DIR

FIXTURES = Path(__file__).parent / "fixtures"
LAYOUTS = ["2012", "2016", "2025", "2026"]


def fixture(year: str) -> Path:
    return FIXTURES / f"eex_{year}_layout.xlsx"


@pytest.mark.parametrize("year", LAYOUTS)
def test_every_layout_parses_to_the_common_columns(year):
    wb = eex.read_auction_file(fixture(year))
    t = wb.table
    assert list(t.columns) == eex.OUTPUT_COLUMNS
    assert len(t) == 12
    assert t["price_eur"].dtype == float and t["volume_t"].notna().all()
    assert t["country"].notna().all()
    assert wb.sheet != "Caption"


def test_known_auction_values():
    t = eex.read_auction_file(fixture("2025")).table
    row = t[(t["date"] == "2025-12-15") & (t["country"] == "EU")].iloc[0]
    assert (row["price_eur"], row["volume_t"], row["cover_ratio"], row["status"]) == (84.6, 3273000, 1.55, "successful")
    assert row["revenue_eur"] == 276895800


def test_country_is_taken_from_the_auction_name_when_the_column_is_missing():
    t = eex.read_auction_file(fixture("2016")).table
    assert set(t["country"]) <= {"EU", "DE", "PL", "HU", "NL", "LT"}
    assert t["certificate"].eq("EUA").all()


def test_real_fixtures_pass_all_checks():
    for year in LAYOUTS:
        wb = eex.read_auction_file(fixture(year))
        eex.check_auctions(wb)
        assert wb.mismatches == []


def corrupted(column: str, value) -> eex.Workbook:
    wb = eex.read_auction_file(fixture("2025"))
    wb.table.loc[0, column] = value
    return wb


@pytest.mark.parametrize(
    "column, value, expected",
    [
        ("cover_ratio", 9.99, "cover ratio"),
        ("revenue_eur", 1.0, "revenue"),
        ("price_eur", 500.0, "outside bids"),
    ],
)
def test_checks_flag_contradictory_auctions(column, value, expected):
    wb = corrupted(column, value)
    eex.check_auctions(wb)
    assert any(expected in m for m in wb.mismatches)


def test_cancelled_auction_without_a_price_is_not_flagged():
    wb = eex.read_auction_file(fixture("2025"))
    wb.table.loc[0, ["price_eur", "revenue_eur", "cover_ratio", "total_bids_t"]] = np.nan
    eex.check_auctions(wb)
    assert wb.mismatches == []


def test_parse_files_raises_on_unknown_mismatch_and_accepts_documented_ones(tmp_path):
    bad = tmp_path / "bad.xlsx"
    raw = pd.read_excel(fixture("2025"), sheet_name=None, header=None, dtype=object)
    sheet, df = next(iter(raw.items()))
    cover_col = next(c for c in df.columns if str(df.iloc[5][c]).startswith("Cover Ratio"))
    df.loc[6, cover_col] = 9.99
    df.to_excel(bad, sheet_name=sheet, header=False, index=False)

    with pytest.raises(eex.AuctionChecksFailed, match="cover ratio"):
        eex.parse_files([bad])
    known = eex.read_auction_file(bad)
    eex.check_auctions(known)
    assert len(eex.parse_files([bad], known_mismatches=known.mismatches)) == 12


def test_duplicate_auctions_raise():
    with pytest.raises(eex.AuctionChecksFailed, match="duplicated"):
        eex.parse_files([fixture("2025"), fixture("2025")])


def test_parse_files_flags_eua_and_sorts_by_date():
    df = eex.parse_files([fixture("2026"), fixture("2012")])
    assert df["date"].is_monotonic_increasing
    assert df["is_eua"].all()
    assert not eex.EUA_CONTRACT.match("EAA3") and eex.EUA_CONTRACT.match("T4PA")


def test_extract_zip_ignores_folders_and_other_files(tmp_path):
    archive = tmp_path / "history.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(fixture("2025"), "reports/emission-spot-2025.xlsx")
        zf.writestr("readme.txt", "not a workbook")
    out = eex.extract_zip(archive, tmp_path / "out")
    assert [p.name for p in out] == ["emission-spot-2025.xlsx"]
    assert len(eex.read_auction_file(out[0]).table) == 12


@pytest.mark.sources
def test_full_history_parses_without_mismatches():
    folder = RAW_DIR / "eex" / "extracted"
    paths = sorted(folder.glob("*.xls*")) + sorted((RAW_DIR / "eex").glob("*-data.xlsx"))
    if not paths:
        pytest.skip("EEX files not downloaded; run `make data`")
    df = eex.parse_files(paths)
    assert df["date"].min().year == 2012 and len(df) > 2900
    assert df.loc[df["price_eur"].isna(), "status"].eq("cancelled").all()


def test_parse_files_raises_when_an_auction_has_no_country(tmp_path, monkeypatch):
    real = eex.read_auction_file(fixture("2025"))
    real.table.loc[0, "country"] = None
    monkeypatch.setattr(eex, "read_auction_file", lambda path: real)
    with pytest.raises(eex.AuctionChecksFailed, match="without a country"):
        eex.parse_files([fixture("2025")])


def test_committed_auctions_have_a_country_everywhere():
    committed = pd.read_parquet(Path(__file__).parents[1] / "data" / "processed" / "eex_auctions.parquet")
    assert committed["country"].notna().all()
