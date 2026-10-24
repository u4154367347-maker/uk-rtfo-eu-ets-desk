import pandas as pd
import pytest

from desk import refuels


def edited(tmp_path, **changes):
    """Copy the real CSV with some cells of its LAST row changed; return the new path."""
    df = pd.read_csv(refuels.MANUAL_FILE, dtype=str, keep_default_na=False)
    for col, value in changes.items():
        df.loc[df.index[-1], col] = value
    path = tmp_path / "prices.csv"
    df.to_csv(path, index=False)
    return path


def test_curated_file_is_valid_and_ascending():
    df = refuels.load_prices()
    assert df["month"].is_monotonic_increasing and df["month"].is_unique
    row = df[df["month"] == "2026-08"].iloc[0]
    assert (row["avg_rtfc_pence"], row["same_month_prior_year_pence"]) == (19.1, 24.9)
    assert row["source_url"].startswith("https://")


def test_every_price_is_below_the_buy_out_cap():
    assert (refuels.load_prices()["avg_rtfc_pence"] <= refuels.MAX_PLAUSIBLE_PENCE).all()


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"avg_rtfc_pence": "91.9"}, "outside"),
        ({"avg_rtfc_pence": "18.1"}, "does not appear in the quoted sentence"),
        ({"same_month_prior_year_pence": "30"}, "does not appear in the quoted sentence"),
        ({"published": "2027-06-01"}, "days after month end"),
        ({"published": "2026-08-01"}, "days after month end"),
        ({"month": "2026-07"}, "duplicated"),
        ({"source_url": "refuels.com"}, "not a URL"),
        ({"month": "Aug 2026"}, "unparseable"),
    ],
)
def test_inconsistent_rows_are_rejected(tmp_path, changes, message):
    with pytest.raises(refuels.CurationError, match=message):
        refuels.load_prices(edited(tmp_path, **changes))


def test_restated_previous_month_must_match_its_own_release(tmp_path):
    df = pd.read_csv(refuels.MANUAL_FILE, dtype=str, keep_default_na=False)
    april = df.index[df["month"] == "2026-04"][0]
    df.loc[april, "prior_month_pence"] = "19.5"
    df.loc[april, "quote"] = df.loc[april, "quote"].replace("19.14", "19.5")
    path = tmp_path / "prices.csv"
    df.to_csv(path, index=False)
    with pytest.raises(refuels.CurationError, match="previous month was 19.5p"):
        refuels.load_prices(path)


def test_wrong_columns_are_rejected(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text("month,price\n2026-08,19.1\n")
    with pytest.raises(refuels.CurationError, match="columns"):
        refuels.load_prices(path)
