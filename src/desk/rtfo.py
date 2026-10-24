"""Parse the DfT RTFO statistics data tables (RF_01, ODS) into tidy tables.

Each RF_01 worksheet has a few lines of title and notes, then one header row, then the
table. Every parser below returns a long ("tidy") DataFrame and checks that the totals
DfT publishes equal the sum of their components, so a layout change fails loudly.

Source: https://www.gov.uk/government/statistical-data-sets/renewable-transport-fuel-obligation-rtfo-statistics-data-tables
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Shorthand markers used by DfT in place of numbers. "[low]" cells still hold the numeric
# value in the file, so in practice only "[x]" (not applicable) and "[No Data]" appear in cells.
SHORTHAND = re.compile(r"^\[(x|low|low%|No Data|c|z|u|r)\]$")

# Supply periods run from the 15th of one month to the 14th of the next (January starts on
# the 1st, December ends on the 31st), so DfT labels them "Jan", "Jan to Feb", ..., "Dec".
SUPPLY_PERIODS = [
    "Jan", "Jan to Feb", "Feb to Mar", "Mar to Apr", "Apr to May", "May to Jun", "Jun to Jul",
    "Jul to Aug", "Aug to Sep", "Sep to Oct", "Oct to Nov", "Nov to Dec", "Dec",
]


class TotalsMismatch(ValueError):
    """A published total does not equal the sum of its components."""


@dataclass
class Sheet:
    table_id: str
    title: str
    data_as_of: str | None  # ISO date of "information received by the DfT as of ..."
    body: pd.DataFrame      # rows below the header row, with cleaned column labels
    mismatches: list[str] = field(default_factory=list)  # filled by the totals checks


def _clean_label(value) -> str:
    text = str(value)
    text = re.sub(r"\s*\[Note \d+\]", "", text).strip()
    if re.fullmatch(r"\d{4}\.0", text):  # year headers read as floats
        text = text[:4]
    return text


def _to_number(value) -> float:
    if isinstance(value, str):
        if SHORTHAND.match(value.strip()):
            return np.nan
        raise ValueError(f"unexpected text in a numeric cell: {value!r}")
    return float(value)


def read_sheet(path: Path, sheet: str) -> Sheet:
    raw = pd.read_excel(path, engine="odf", sheet_name=sheet, header=None, dtype=object)

    # The header is the first row with several filled cells; title and note rows have one.
    filled = raw.notna().sum(axis=1)
    header_idx = int(filled[filled >= 4].index[0])

    preamble = " ".join(str(v) for v in raw.iloc[:header_idx, 0].dropna())
    as_of = re.search(r"as of the (\d{1,2} \w+ \d{4})", preamble)
    data_as_of = datetime.strptime(as_of.group(1), "%d %B %Y").date().isoformat() if as_of else None

    body = raw.iloc[header_idx + 1 :].copy()
    body.columns = [_clean_label(c) for c in raw.iloc[header_idx]]
    body = body.dropna(how="all").reset_index(drop=True)
    body = body.drop(columns=[c for c in body.columns if c == "Notes"])
    return Sheet(table_id=sheet, title=str(raw.iat[0, 0]), data_as_of=data_as_of, body=body)


def _year_from_title(title: str) -> int:
    years = re.findall(r"\b(20\d{2})\b", title)
    if not years:
        raise ValueError(f"no year in title: {title!r}")
    return int(years[-1])


def _check_sum(sheet: Sheet, parts: pd.Series, total: float, what: str, tol: float = 1e-3) -> None:
    """Record (not raise) a mismatch, so one run reports all of them; see parse_workbook."""
    expected = parts.sum(min_count=1)
    if pd.isna(total) and pd.isna(expected):
        return
    if not np.isclose(expected, total, rtol=1e-6, atol=tol):
        sheet.mismatches.append(f"{what}: components sum to {expected:.6f}, published total is {total:.6f}")


# --- RF_0101: fuel volumes ------------------------------------------------------------------

def parse_volumes(sheet: Sheet) -> pd.DataFrame:
    """RF_0101: volume of fuel supplied by fuel type and supply period (million litres equivalent)."""
    b = sheet.body.rename(columns={"Renewable status": "renewable_status", "Fuel type": "fuel_type"})
    values = b[SUPPLY_PERIODS + ["Total"]].map(_to_number)
    b = pd.concat([b[["renewable_status", "fuel_type"]], values], axis=1)
    label = sheet.table_id

    for _, row in b.iterrows():
        _check_sum(sheet, row[SUPPLY_PERIODS], row["Total"], f"{label} {row['fuel_type']} supply periods")

    total_of = {}
    for status, group in b.groupby("renewable_status", sort=False):
        is_total = group["fuel_type"].str.startswith("Total")
        if is_total.sum() != 1:
            raise TotalsMismatch(f"{label} {status}: expected exactly one total row")
        total_row = group[is_total].iloc[0]
        total_of[status] = total_row
        if (~is_total).any():
            for col in SUPPLY_PERIODS + ["Total"]:
                _check_sum(sheet, group.loc[~is_total, col], total_row[col], f"{label} {status} {col}")
    for col in SUPPLY_PERIODS + ["Total"]:
        _check_sum(
            sheet, pd.Series([total_of["Regular renewable fuel"][col], total_of["Development renewable fuel"][col]]),
            total_of["Renewable fuel"][col], f"{label} renewable total {col}",
        )
        _check_sum(
            sheet, pd.Series([total_of["Fossil fuel"][col], total_of["Renewable fuel"][col]]),
            total_of["All fuel"][col], f"{label} all fuel {col}",
        )

    long = b.melt(
        id_vars=["renewable_status", "fuel_type"], value_vars=SUPPLY_PERIODS,
        var_name="supply_period", value_name="value",
    )
    long["supply_period_index"] = long["supply_period"].map(SUPPLY_PERIODS.index)
    long["is_total"] = long["fuel_type"].str.startswith("Total")
    long["obligation_period"] = _year_from_title(sheet.title)
    long["unit"] = "million litres (equivalent)"
    return long


# --- RF_0102: certificates issued -----------------------------------------------------------

_RF0102_MEASURES = {
    "Number of RTFCs issued (millions)": ("rtfcs_issued", "million certificates"),
    "Fuel volumes (RTFCs issued) (millions)": ("volume_with_rtfcs", "million litres (equivalent)"),
    "Total volume of renewable fuel": ("renewable_volume_total", "million litres (equivalent)"),
    "Percentage of renewable fuel to which RTFCs have been issued": ("share_with_rtfcs", "fraction"),
}
_RF0102_SHARE = "Percentage of renewable fuel to which RTFCs have been issued"


def parse_certificates_issued(sheet: Sheet) -> pd.DataFrame:
    """RF_0102: RTFCs issued and the related fuel volumes, by category and supply period."""
    b = sheet.body.copy()
    b.columns = ["measure", "rtfc_category", *b.columns[2:]]
    unknown = set(b["measure"]) - set(_RF0102_MEASURES)
    if unknown:
        raise ValueError(f"{sheet.table_id}: unknown measures {sorted(unknown)}")
    values = b[SUPPLY_PERIODS + ["Total"]].map(_to_number)
    b = pd.concat([b[["measure", "rtfc_category"]], values], axis=1)

    for measure in ["Number of RTFCs issued (millions)", "Fuel volumes (RTFCs issued) (millions)"]:
        group = b[b["measure"] == measure]
        total_row = group[group["rtfc_category"] == "Total"].iloc[0]
        parts = group[group["rtfc_category"] != "Total"]
        for col in SUPPLY_PERIODS + ["Total"]:
            _check_sum(sheet, parts[col], total_row[col], f"{sheet.table_id} {measure} {col}")
    for _, row in b[b["measure"] != _RF0102_SHARE].iterrows():
        _check_sum(sheet, row[SUPPLY_PERIODS], row["Total"], f"{sheet.table_id} {row['measure']} {row['rtfc_category']}")

    long = b.melt(
        id_vars=["measure", "rtfc_category"], value_vars=SUPPLY_PERIODS,
        var_name="supply_period", value_name="value",
    )
    long["unit"] = long["measure"].map(lambda m: _RF0102_MEASURES[m][1])
    long["measure"] = long["measure"].map(lambda m: _RF0102_MEASURES[m][0])
    long["supply_period_index"] = long["supply_period"].map(SUPPLY_PERIODS.index)
    long["is_total"] = long["rtfc_category"].isin(["Total", "Percent"])
    long["obligation_period"] = _year_from_title(sheet.title)
    return long



# --- RF_0103: certificate balances ----------------------------------------------------------

def parse_balances(sheet: Sheet) -> pd.DataFrame:
    """RF_0103: RTFCs issued, revoked, redeemed and still in existence, by obligation period."""
    b = sheet.body.copy()
    b.columns = ["status", "rtfc_category", *b.columns[2:]]
    year_cols = list(b.columns[2:])
    for _, group in b.groupby("status", sort=False):
        is_total = group["rtfc_category"].str.startswith("Total")
        if is_total.any():
            for col in year_cols:
                _check_sum(sheet, group.loc[~is_total, col].map(_to_number),
                           _to_number(group.loc[is_total, col].iloc[0]),
                           f"{sheet.table_id} {group['status'].iloc[0]} {col}")

    long = b.melt(id_vars=["status", "rtfc_category"], var_name="period_label", value_name="value")
    long["value"] = long["value"].map(_to_number)
    long["obligation_period"] = long["period_label"].str.extract(r"(\d{4})", expand=False).astype(int)
    long["period_is_provisional"] = long["period_label"].str.contains("provisional")
    long["is_total"] = long["rtfc_category"].str.startswith("Total") | (long["rtfc_category"] == "All categories")
    long["unit"] = "million certificates"
    return long.drop(columns="period_label")


# --- RF_0104: certificate transfers ---------------------------------------------------------

def parse_transfers(sheet: Sheet) -> pd.DataFrame:
    """RF_0104: RTFCs transferred by quarter, certificate category and account holder type."""
    b = sheet.body.copy()
    b.columns = ["obligation_period", "quarter", "account_types", *b.columns[3:]]
    categories = [c for c in b.columns[3:] if c != "Total"]
    values = b[categories + ["Total"]].map(_to_number)
    b = pd.concat([b[["obligation_period", "quarter", "account_types"]], values], axis=1)
    b["obligation_period"] = b["obligation_period"].astype(int)

    for _, row in b.iterrows():
        _check_sum(sheet, row[categories], row["Total"], f"{sheet.table_id} {row['quarter']} {row['account_types']}")
    for (period, quarter), group in b.groupby(["obligation_period", "quarter"], sort=False):
        is_all = group["account_types"] == "All account types"
        for col in categories + ["Total"]:
            _check_sum(sheet, group.loc[~is_all, col], group.loc[is_all, col].iloc[0],
                       f"{sheet.table_id} {period} {quarter} {col}")
    for (period, account), group in b.groupby(["obligation_period", "account_types"], sort=False):
        is_all = group["quarter"] == "All quarters"
        if is_all.any():
            for col in categories + ["Total"]:
                _check_sum(sheet, group.loc[~is_all, col], group.loc[is_all, col].iloc[0],
                           f"{sheet.table_id} {period} {account} all quarters {col}")

    long = b.melt(
        id_vars=["obligation_period", "quarter", "account_types"], value_vars=categories + ["Total"],
        var_name="rtfc_category", value_name="value",
    )
    # RF_0104 says "Development Fuels"; every other table says "Development Fuel".
    long["rtfc_category"] = (
        long["rtfc_category"].str.replace("RTFC Category: ", "", regex=False)
        .replace({"Development Fuels": "Development Fuel"})
    )
    long["is_total"] = (
        (long["rtfc_category"] == "Total")
        | (long["account_types"] == "All account types")
        | (long["quarter"] == "All quarters")
    )
    long["unit"] = "million certificates"
    return long


# --- RF_0107: performance against obligation (final releases only) --------------------------

_RF0107_KINDS = {
    "RTFCs": ("main", "rtfcs"),
    "RTFC": ("main", "rtfcs"),
    "Buy-out": ("main", "buy_out"),
    "Main Buy-out": ("main", "buy_out"),
    "dRTFCs": ("development", "rtfcs"),
    "Dev Buy-out": ("development", "buy_out"),
}


def parse_obligation_performance(sheet: Sheet) -> pd.DataFrame:
    """RF_0107: share of each obligated supplier's obligation met with certificates vs buy-out."""
    b = sheet.body.rename(columns={"Obligated suppliers": "supplier"})
    pattern = re.compile(r"^(?P<period>.+?) (?P<kind>" + "|".join(sorted(_RF0107_KINDS, key=len, reverse=True)) + r")$")
    columns = []
    for col in b.columns[1:]:
        m = pattern.match(col)
        if not m:
            raise ValueError(f"{sheet.table_id}: unexpected column {col!r}")
        obligation, route = _RF0107_KINDS[m["kind"]]
        columns.append((col, m["period"], obligation, route))

    long = b.melt(id_vars="supplier", var_name="column", value_name="share")
    meta = pd.DataFrame(columns, columns=["column", "obligation_period_label", "obligation", "route"])
    long = long.merge(meta, on="column").drop(columns="column")
    long["share"] = long["share"].map(_to_number)
    long = long.dropna(subset=["share"]).reset_index(drop=True)

    pairs = long.pivot_table(index=["supplier", "obligation_period_label", "obligation"],
                             columns="route", values="share")
    off = (pairs["rtfcs"] + pairs["buy_out"] - 1).abs() > 0.002
    for supplier, period, obligation in pairs[off].index:
        sheet.mismatches.append(f"{sheet.table_id} {supplier} {period} {obligation}: RTFC and buy-out shares do not add to 1")
    long["unit"] = "fraction of supplier obligation"
    return long


# --- Workbook -------------------------------------------------------------------------------

PARSERS = {
    "RF_0101": ("volumes", parse_volumes),
    "RF_0102": ("certificates_issued", parse_certificates_issued),
    "RF_0103": ("balances", parse_balances),
    "RF_0104": ("transfers", parse_transfers),
    "RF_0107": ("obligation_performance", parse_obligation_performance),
}


def parse_workbook(
    path: Path, release: str, release_status: str, known_mismatches: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Parse every supported table present in one RF_01 workbook.

    Any totals mismatch not listed in `known_mismatches` (config/sources.yaml) raises
    TotalsMismatch. Known ones are inconsistencies in DfT's own file, documented in the config.
    """
    available = set(pd.ExcelFile(path, engine="odf").sheet_names)
    known = set(known_mismatches or [])
    unexpected = []
    tables = {}
    for table_id, (name, parser) in PARSERS.items():
        if table_id not in available:
            continue  # e.g. RF_0107 is only published with final releases
        sheet = read_sheet(path, table_id)
        df = parser(sheet)
        df.insert(0, "release", release)
        df.insert(1, "release_status", release_status)
        df.insert(2, "data_as_of", sheet.data_as_of)
        df.insert(3, "table_id", table_id)
        tables[name] = df
        unexpected += [m for m in sheet.mismatches if m not in known]
    if unexpected:
        raise TotalsMismatch(f"{release}: " + "; ".join(unexpected))
    return tables
