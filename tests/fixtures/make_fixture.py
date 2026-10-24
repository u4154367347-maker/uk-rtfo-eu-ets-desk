"""Build tests/fixtures/rf01_sample.ods: real RF_01 sheets, cut down so tests run offline.

Run once from the repo root after `make data`:  python tests/fixtures/make_fixture.py
RF_0101-RF_0103 are copied whole from the 2026 first provisional release, RF_0104 keeps its
first two quarters, and RF_0107 (final releases only) keeps the first five suppliers of 2024 final.
"""

from pathlib import Path

import pandas as pd

RAW = Path("data/raw/rtfo")
OUT = Path(__file__).with_name("rf01_sample.ods")


def raw_sheet(path: Path, sheet: str) -> pd.DataFrame:
    return pd.read_excel(path, engine="odf", sheet_name=sheet, header=None, dtype=object)


def main() -> None:
    p2026 = RAW / "2026-first-provisional-rf-01-rtfo-tables.ods"
    p2024 = RAW / "2024-final-rf-01-rtfo-tables-revised.ods"
    sheets = {s: raw_sheet(p2026, s) for s in ["RF_0101", "RF_0102", "RF_0103"]}
    sheets["RF_0104"] = raw_sheet(p2026, "RF_0104").iloc[:19]   # title + header + 2 quarters
    sheets["RF_0107"] = raw_sheet(p2024, "RF_0107").iloc[:13]   # title + header + 5 suppliers
    with pd.ExcelWriter(OUT, engine="odf") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, header=False, index=False)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
