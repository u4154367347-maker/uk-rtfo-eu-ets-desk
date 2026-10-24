"""Build the EEX test fixtures: real auction rows, cut down so tests run offline.

Run from the repo root after `make data`:  python tests/fixtures/make_eex_fixtures.py
One file per layout EEX has used: 2012 (short, "Minimal Price"), 2016 (EUR headers,
"Certificate" column, plus the Caption sheet), 2025 (wide, with "Status") and 2026 (sheet
"EU ETS1", "Zone" instead of "Country"). Each keeps the title/header rows and the first
12 auctions of its source.
"""

import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
RAW = Path("data/raw/eex")
OUT = Path(__file__).parent
SOURCES = {
    "eex_2012_layout.xlsx": (RAW / "extracted" / "emission-spot-primary-market-auction-report-2012-data.xls", 3),
    "eex_2016_layout.xlsx": (RAW / "extracted" / "emission-spot-primary-market-auction-report-2016-data.xls", 3),
    "eex_2025_layout.xlsx": (RAW / "extracted" / "emission-spot-primary-market-auction-report-2025-data.xlsx", 6),
    "eex_2026_layout.xlsx": (RAW / "emission-spot-primary-market-auction-report-2026-data.xlsx", 6),
}


def main() -> None:
    for name, (src, header_rows) in SOURCES.items():
        sheets = pd.read_excel(src, sheet_name=None, header=None, dtype=object)
        with pd.ExcelWriter(OUT / name) as writer:
            for sheet, df in sheets.items():
                keep = df.iloc[: header_rows + 12] if sheet.lower() != "caption" else df
                keep.to_excel(writer, sheet_name=sheet, header=False, index=False)
        print("wrote", name)


if __name__ == "__main__":
    main()
