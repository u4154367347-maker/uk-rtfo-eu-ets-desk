"""Parse the EEX EUA primary auction history into one tidy table (one row per auction).

EEX publishes one workbook per year (2012-2019 as .xls, 2020 onwards as .xlsx). The layout
drifts between years (header row position, column names, extra columns), so columns are
matched by name patterns, not by position, and every parsed auction is checked:

* cover ratio = total bids / auction volume (as published, rounded to 2 decimals)
* revenue = auction price x auction volume
* minimum bid <= auction price <= maximum bid

Source: https://www.eex.com/en/market-data/market-data-hub/environmentals-data
"""

from __future__ import annotations

import re
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# Pattern on the lower-cased header text -> output column. First match wins, so the more
# specific patterns ("bidders") come before the generic ones ("bids").
COLUMN_PATTERNS: list[tuple[str, str]] = [
    (r"^date$", "date"),
    (r"^(auction )?time$", "time"),
    (r"^auction name$", "auction_name"),
    (r"^contract$", "contract"),
    (r"^certificate$", "certificate"),
    (r"^status$", "status"),
    (r"^auction price", "price_eur"),
    (r"^(minimum bid|minimal price)", "min_bid_eur"),
    (r"^(maximum bid|maximal price)", "max_bid_eur"),
    (r"^mean", "mean_price_eur"),
    (r"^median", "median_price_eur"),
    (r"^auction volume", "volume_t"),
    (r"^total amount of bids", "total_bids_t"),
    (r"^number of bids submitted", "n_bids_submitted"),
    (r"^number of succ\w* bids$", "n_bids_successful"),
    (r"^cover ratio", "cover_ratio"),
    (r"^total number of bidders", "n_bidders"),
    (r"^number of succ\w*\s+bidders", "n_successful_bidders"),
    (r"^total revenue", "revenue_eur"),
    (r"^(country|zone)$", "country"),
]
REQUIRED = ["date", "auction_name", "contract", "price_eur", "volume_t", "total_bids_t", "cover_ratio", "revenue_eur"]
NUMERIC = [
    "price_eur", "min_bid_eur", "max_bid_eur", "mean_price_eur", "median_price_eur", "volume_t",
    "total_bids_t", "n_bids_submitted", "n_bids_successful", "cover_ratio", "n_bidders",
    "n_successful_bidders", "revenue_eur",
]
OUTPUT_COLUMNS = ["date", "auction_name", "contract", "certificate", "status", *NUMERIC, "country"]

# Contract codes seen in the files: T2PA/T3PA/T4PA = EUA phases 2-4, EAA2/EAA3 = aviation allowances.
EUA_CONTRACT = re.compile(r"^T\dPA$")


class AuctionChecksFailed(ValueError):
    """Parsed auctions contradict their own published totals."""


@dataclass
class Workbook:
    source_file: str
    sheet: str
    table: pd.DataFrame
    mismatches: list[str] = field(default_factory=list)


def _normalise_header(text) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\n", " ")).strip().lower()


def _map_columns(headers: list) -> dict[int, str]:
    mapping: dict[int, str] = {}
    taken: set[str] = set()
    for idx, header in enumerate(headers):
        text = _normalise_header(header)
        for pattern, name in COLUMN_PATTERNS:
            if name not in taken and re.search(pattern, text):
                mapping[idx] = name
                taken.add(name)
                break
    return mapping


def read_auction_file(path: Path) -> Workbook:
    """Read one yearly workbook into the common column set."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl: "Workbook contains no default style"
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    candidates = {n: df for n, df in sheets.items() if n.strip().lower() != "caption"}
    if len(candidates) != 1:
        raise ValueError(f"{path.name}: expected one data sheet, found {list(candidates)}")
    (sheet, raw), = candidates.items()

    header_row = next(
        (i for i in range(min(15, len(raw))) if raw.iloc[i].astype(str).str.lower().str.contains("auction price").any()),
        None,
    )
    if header_row is None:
        raise ValueError(f"{path.name}: no header row containing 'Auction Price'")
    mapping = _map_columns(raw.iloc[header_row].tolist())
    missing = [c for c in REQUIRED if c not in mapping.values()]
    if missing:
        raise ValueError(f"{path.name}: columns not found: {missing}")

    body = raw.iloc[header_row + 1 :, list(mapping)].copy()
    body.columns = list(mapping.values())
    body = body.dropna(subset=["date", "contract"]).reset_index(drop=True)
    body["date"] = pd.to_datetime(body["date"]).dt.normalize()
    for col in NUMERIC:
        if col in body:
            body[col] = pd.to_numeric(body[col], errors="coerce")
    for col in OUTPUT_COLUMNS:
        if col not in body:
            body[col] = np.nan if col in NUMERIC else None
    body["contract"] = body["contract"].astype(str).str.strip()
    body["auction_name"] = body["auction_name"].astype(str).str.strip()
    # Same-day auctions on different platforms (EU common platform, DE, PL, ...) are separate
    # auctions. Some years have no country column, so take it from the end of the auction name.
    body["country"] = body["country"].where(
        body["country"].notna(), body["auction_name"].str.extract(r"(?:^|[\s-])([A-Z]{2})(?:\s*\(.*\))?$", expand=False)
    )
    return Workbook(source_file=path.name, sheet=sheet, table=body[OUTPUT_COLUMNS])


def check_auctions(wb: Workbook) -> None:
    """Record (not raise) every auction that contradicts its own published figures."""
    t = wb.table
    priced = t["price_eur"].notna() & t["volume_t"].notna()

    cover = t["total_bids_t"] / t["volume_t"]
    has_cover = priced & t["total_bids_t"].notna() & t["cover_ratio"].notna()  # old auctions lack some fields
    bad = has_cover & ~np.isclose(cover, t["cover_ratio"], atol=0.0051, rtol=0)
    for _, r in t[bad].iterrows():
        wb.mismatches.append(
            f"{wb.source_file} {r['date']:%Y-%m-%d} {r['auction_name']} {r['contract']}: "
            f"cover ratio {r['cover_ratio']} vs bids/volume {r['total_bids_t'] / r['volume_t']:.4f}"
        )

    revenue = t["price_eur"] * t["volume_t"]
    bad = priced & t["revenue_eur"].notna() & ~np.isclose(revenue, t["revenue_eur"], atol=1.0, rtol=1e-9)
    for _, r in t[bad].iterrows():
        wb.mismatches.append(
            f"{wb.source_file} {r['date']:%Y-%m-%d} {r['auction_name']} {r['contract']}: "
            f"revenue {r['revenue_eur']:.0f} vs price x volume {r['price_eur'] * r['volume_t']:.0f}"
        )

    inside = t["min_bid_eur"].isna() | t["max_bid_eur"].isna() | (
        (t["min_bid_eur"] <= t["price_eur"] + 1e-9) & (t["price_eur"] <= t["max_bid_eur"] + 1e-9)
    )
    for _, r in t[priced & ~inside].iterrows():
        wb.mismatches.append(
            f"{wb.source_file} {r['date']:%Y-%m-%d} {r['auction_name']} {r['contract']}: "
            f"price {r['price_eur']} outside bids [{r['min_bid_eur']}, {r['max_bid_eur']}]"
        )


def parse_files(paths: list[Path], known_mismatches: list[str] | None = None) -> pd.DataFrame:
    """Parse and check workbooks; return all auctions, oldest first."""
    frames, mismatches = [], []
    for path in paths:
        wb = read_auction_file(path)
        check_auctions(wb)
        mismatches += wb.mismatches
        frames.append(wb.table.assign(source_file=wb.source_file))
    unexpected = [m for m in mismatches if m not in set(known_mismatches or [])]
    if unexpected:
        raise AuctionChecksFailed(f"{len(unexpected)} unexpected: " + "; ".join(unexpected[:10]))

    df = pd.concat(frames, ignore_index=True)
    no_country = df[df["country"].isna()]
    if len(no_country):
        # Same-day auctions on different platforms are only separable by country, so a missing one is not usable.
        raise AuctionChecksFailed(f"{len(no_country)} auctions without a country, e.g. "
                                  f"{no_country[['date', 'auction_name']].head(3).astype(str).values.tolist()}")
    key = ["date", "auction_name", "contract"]
    dupes = df[df.duplicated(key, keep=False)]
    if len(dupes):
        raise AuctionChecksFailed(f"{len(dupes)} duplicated auctions, e.g. {dupes[key].head(3).values.tolist()}")
    df["is_eua"] = df["contract"].str.match(EUA_CONTRACT)
    return df.sort_values(["date", "auction_name", "contract"]).reset_index(drop=True)


def extract_zip(zip_path: Path, dest: Path) -> list[Path]:
    """Extract the yearly workbooks of an EEX archive into `dest`, returning their paths."""
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name  # ignore any directory structure inside the zip
            if not name.lower().endswith((".xls", ".xlsx")):
                continue
            target = dest / name
            target.write_bytes(zf.read(info))
            out.append(target)
    return sorted(out)
