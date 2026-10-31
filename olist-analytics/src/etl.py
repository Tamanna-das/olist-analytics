"""Load the nine Olist CSVs into a fresh SQLite database.

Usage:
    python src/etl.py                       # data/raw -> data/olist.db
    python src/etl.py --raw path/to/csvs --db data/olist.db

Steps: run sql/01_schema.sql (drops and recreates everything), load each
CSV with light, documented cleaning, then print row counts.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# csv file name -> (table name, column renames)
SOURCES: dict[str, tuple[str, dict[str, str]]] = {
    "olist_customers_dataset.csv": ("customers", {}),
    "olist_geolocation_dataset.csv": ("geolocation", {}),
    "olist_sellers_dataset.csv": ("sellers", {}),
    "product_category_name_translation.csv": ("category_translation", {}),
    "olist_products_dataset.csv": (
        "products",
        {
            "product_name_lenght": "product_name_length",
            "product_description_lenght": "product_description_length",
        },
    ),
    "olist_orders_dataset.csv": ("orders", {}),
    "olist_order_items_dataset.csv": ("order_items", {}),
    "olist_order_payments_dataset.csv": ("order_payments", {}),
    "olist_order_reviews_dataset.csv": ("order_reviews", {}),
}

ZIP_COLUMNS = {
    "customer_zip_code_prefix",
    "seller_zip_code_prefix",
    "geolocation_zip_code_prefix",
}

TIMESTAMP_COLUMNS = {
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
    "shipping_limit_date",
    "review_creation_date",
    "review_answer_timestamp",
}


def clean(df: pd.DataFrame, renames: dict[str, str]) -> pd.DataFrame:
    df = df.rename(columns=lambda c: c.strip().lstrip("﻿")).rename(columns=renames)
    for col in df.columns:
        if col in ZIP_COLUMNS:
            # CSVs store zip prefixes as ints and lose leading zeros.
            df[col] = df[col].astype("string").str.strip().str.zfill(5)
        elif col in TIMESTAMP_COLUMNS:
            ts = pd.to_datetime(df[col], errors="coerce")
            df[col] = ts.dt.strftime("%Y-%m-%d %H:%M:%S").where(ts.notna(), None)
    return df


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


def load(raw_dir: Path, db_path: Path) -> None:
    missing = [f for f in SOURCES if not (raw_dir / f).exists()]
    if missing:
        sys.exit(f"Missing CSVs in {raw_dir}:\n  " + "\n  ".join(missing))

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript((ROOT / "sql" / "01_schema.sql").read_text())

    for fname, (table, renames) in SOURCES.items():
        df = pd.read_csv(raw_dir / fname, dtype=str, encoding="utf-8-sig", keep_default_na=True)
        df = clean(df, renames)
        cols = table_columns(con, table)
        extra = set(df.columns) - set(cols)
        if extra:
            print(f"  ! {fname}: ignoring unexpected columns {sorted(extra)}")
        df = df[[c for c in cols if c in df.columns]]
        df = df.astype(object).where(df.notna(), None)
        placeholders = ",".join("?" * len(df.columns))
        con.executemany(
            f"INSERT INTO {table} ({','.join(df.columns)}) VALUES ({placeholders})",
            df.itertuples(index=False, name=None),
        )
        con.commit()
        print(f"  {table:<22} {len(df):>9,} rows  <- {fname}")

    con.execute("ANALYZE")
    con.close()
    print(f"\nLoaded into {db_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "olist.db")
    args = ap.parse_args()
    load(args.raw, args.db)


if __name__ == "__main__":
    main()
