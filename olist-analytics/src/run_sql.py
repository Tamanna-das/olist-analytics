"""Run the analysis SQL files (02-06) top to bottom and save every result.

Each query in a .sql file is preceded by a "-- @name <id>" tag. Results
go to reports/sql_outputs/<id>.csv and a preview is printed.

Usage:
    python src/run_sql.py                    # all of 02..06
    python src/run_sql.py sql/06_delivery_delay.sql --rows 20
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILES = sorted(p for p in (ROOT / "sql").glob("0*.sql") if not p.name.startswith("01_"))
NAME_RE = re.compile(r"^--\s*@name\s+(\w+)\s*$", re.MULTILINE)


def split_named_queries(sql_text: str) -> list[tuple[str, str]]:
    """Return [(name, sql)] for every '-- @name' block in a file."""
    marks = list(NAME_RE.finditer(sql_text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(sql_text)
        body = sql_text[m.end():end].strip().rstrip(";").strip()
        out.append((m.group(1), body))
    return out


def run(files: list[Path], db: Path, out_dir: Path, rows: int) -> None:
    if not db.exists():
        raise SystemExit(f"{db} not found — run `python src/etl.py` first.")
    con = sqlite3.connect(db)
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 20)

    total = 0
    for f in files:
        print(f"\n{'=' * 72}\n{f.name}\n{'=' * 72}")
        for name, sql in split_named_queries(f.read_text()):
            t0 = time.perf_counter()
            df = pd.read_sql_query(sql, con)
            df.to_csv(out_dir / f"{name}.csv", index=False)
            total += 1
            print(f"\n-- {name}  ({len(df)} rows, {time.perf_counter() - t0:.2f}s)")
            print(df.head(rows).to_string(index=False))
    con.close()
    print(f"\n{total} queries OK -> {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", type=Path, default=DEFAULT_FILES)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "olist.db")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "sql_outputs")
    ap.add_argument("--rows", type=int, default=12, help="preview rows per query")
    args = ap.parse_args()
    run(args.files, args.db, args.out, args.rows)


if __name__ == "__main__":
    main()
