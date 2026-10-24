"""Load and validate the hand-curated ReFuels monthly average RTFC price.

ReFuels (biomethane fuel retailer) states the month's average RTFC price in its monthly
"Biomethane Station Data" press release. It is one seller's number quoted in a press release,
not a market index, and there is no download, so the series is a CSV with one row per release.
Every row carries its source URL and the exact sentence it was read from, and the loader
checks that the sentence contains the number.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from desk.io import REPO_ROOT

MANUAL_FILE = REPO_ROOT / "data" / "manual" / "refuels_rtfc_prices.csv"
COLUMNS = [
    "month", "avg_rtfc_pence", "same_month_prior_year_pence", "prior_month_pence",
    "published", "source_url", "quote", "notes",
]
# An RTFC trades below the main-obligation buy-out (50p per certificate in 2026), because buying
# out is the alternative. A higher price is more likely a typo than a market price.
MAX_PLAUSIBLE_PENCE = 50.0
MAX_DAYS_TO_PUBLISH = 60


class CurationError(ValueError):
    """The curated CSV is inconsistent with itself or with its quoted sources."""


def _appears_in(number: float, text: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(f'{number:g}')}(?![\d.])", text) is not None


def load_prices(path: Path = MANUAL_FILE) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"month": str, "published": str})
    if list(df.columns) != COLUMNS:
        raise CurationError(f"{path.name}: columns are {list(df.columns)}, expected {COLUMNS}")
    problems: list[str] = []

    df["month_start"] = pd.to_datetime(df["month"], format="%Y-%m", errors="coerce")
    df["published"] = pd.to_datetime(df["published"], format="%Y-%m-%d", errors="coerce")
    for col in ("month_start", "published"):
        if df[col].isna().any():
            problems.append(f"unparseable {'month' if col == 'month_start' else col} in rows {list(df.index[df[col].isna()] + 2)}")
    if df["month"].duplicated().any():
        problems.append(f"duplicated months {sorted(df['month'][df['month'].duplicated()])}")
    if not df["month"].is_monotonic_increasing:
        problems.append("months are not in ascending order")

    for _, r in df.iterrows():
        label = r["month"]
        price = r["avg_rtfc_pence"]
        if not (0 < price <= MAX_PLAUSIBLE_PENCE):
            problems.append(f"{label}: price {price} outside (0, {MAX_PLAUSIBLE_PENCE}]")
        if not str(r["source_url"]).startswith("http"):
            problems.append(f"{label}: source_url is not a URL")
        quote = str(r["quote"])
        for col in ("avg_rtfc_pence", "same_month_prior_year_pence", "prior_month_pence"):
            if pd.notna(r[col]) and not _appears_in(r[col], quote):
                problems.append(f"{label}: {col} {r[col]:g} does not appear in the quoted sentence")
        if pd.notna(r["month_start"]) and pd.notna(r["published"]):
            month_end = r["month_start"] + pd.offsets.MonthEnd(0)
            lag = (r["published"] - month_end).days
            if not 0 <= lag <= MAX_DAYS_TO_PUBLISH:
                problems.append(f"{label}: published {r['published']:%Y-%m-%d} is {lag} days after month end")

    # A later release that restates the previous month must agree with that month's own release.
    by_month = df.set_index("month")["avg_rtfc_pence"]
    for _, r in df[df["prior_month_pence"].notna()].iterrows():
        previous = (r["month_start"] - pd.offsets.MonthBegin(1)).strftime("%Y-%m")
        if previous in by_month.index and not np.isclose(by_month[previous], r["prior_month_pence"]):
            problems.append(f"{r['month']}: says previous month was {r['prior_month_pence']:g}p, "
                            f"but {previous} is recorded as {by_month[previous]:g}p")

    if problems:
        raise CurationError(f"{path.name}: " + "; ".join(problems))
    return df.drop(columns="month_start")
