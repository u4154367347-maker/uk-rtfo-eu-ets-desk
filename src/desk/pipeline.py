"""Download and clean every dataset: `python -m desk.pipeline` (or `make data`)."""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from desk import eex, refuels, rtfo
from desk.io import PROCESSED_DIR, RAW_DIR, REPO_ROOT, fetch, load_sources, sha256, write_provenance


def build_rtfo(sources: dict) -> dict[str, pd.DataFrame]:
    records, parts = [], {}
    for entry in sources["rtfo"]["files"]:
        dest = RAW_DIR / "rtfo" / entry["url"].rsplit("/", 1)[-1]
        record = fetch(entry["url"], dest, entry.get("sha256"))
        record.update(release=entry["release"], source_page=sources["rtfo"]["source_page"],
                      published=str(entry.get("published")) if entry.get("published") else None,
                      known_mismatches=entry.get("known_mismatches", []))
        records.append(record)
        tables = rtfo.parse_workbook(dest, entry["release"], entry["status"], entry.get("known_mismatches"))
        for name, df in tables.items():
            parts.setdefault(name, []).append(df)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    tables = {}
    for name, dfs in parts.items():
        df = pd.concat(dfs, ignore_index=True)
        df.to_parquet(PROCESSED_DIR / f"rtfo_{name}.parquet", index=False)
        tables[name] = df
    write_provenance("rtfo", records)
    return tables


DATASETS = ["rtfo", "eex", "refuels"]


def build_eex(sources: dict) -> dict[str, pd.DataFrame]:
    records, workbooks = [], []
    for entry in sources["eex"]["files"]:
        dest = RAW_DIR / "eex" / entry["url"].rsplit("/", 1)[-1]
        record = fetch(entry["url"], dest, entry.get("sha256"), refresh=entry.get("refresh", False))
        record.update(release=entry["release"], source_page=sources["eex"]["source_page"],
                      published=str(entry["published"]) if entry.get("published") else None,
                      pinned=bool(entry.get("sha256")), known_mismatches=entry.get("known_mismatches", []))
        records.append(record)
        workbooks += eex.extract_zip(dest, RAW_DIR / "eex" / "extracted") if entry["kind"] == "zip" else [dest]

    known = [m for entry in sources["eex"]["files"] for m in entry.get("known_mismatches", [])]
    auctions = eex.parse_files(workbooks, known)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    auctions.to_parquet(PROCESSED_DIR / "eex_auctions.parquet", index=False)
    write_provenance("eex", records)
    return {"auctions": auctions}


def build_refuels(sources: dict) -> dict[str, pd.DataFrame]:
    path = REPO_ROOT / sources["refuels"]["manual_file"]
    prices = refuels.load_prices(path)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(PROCESSED_DIR / "refuels_rtfc_prices.parquet", index=False)
    write_provenance("refuels", [{
        "file": str(path.relative_to(REPO_ROOT)), "sha256": sha256(path), "rows": len(prices),
        "first_month": prices["month"].iloc[0], "last_month": prices["month"].iloc[-1],
        "source_page": sources["refuels"]["source_page"], "curated_by_hand": True,
    }])
    return {"rtfc_prices": prices}


def parse_datasets(argv: list[str] | None = None) -> list[str]:
    parser = argparse.ArgumentParser(description=__doc__)
    # No `choices=` here: with nargs="*", Python 3.11 rejects an empty argument list.
    parser.add_argument("datasets", nargs="*", help=f"any of {DATASETS} (default: all)")
    args = parser.parse_args(argv)
    unknown = sorted(set(args.datasets) - set(DATASETS))
    if unknown:
        parser.error(f"unknown datasets {unknown}; choose from {DATASETS}")
    return args.datasets or DATASETS


def main(argv: list[str] | None = None) -> int:
    datasets = parse_datasets(argv)
    sources = load_sources()
    if "rtfo" in datasets:
        for name, df in build_rtfo(sources).items():
            print(f"rtfo_{name}.parquet: {len(df):,} rows")
    if "eex" in datasets:
        for name, df in build_eex(sources).items():
            print(f"eex_{name}.parquet: {len(df):,} rows")
    if "refuels" in datasets:
        for name, df in build_refuels(sources).items():
            print(f"refuels_{name}.parquet: {len(df):,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
