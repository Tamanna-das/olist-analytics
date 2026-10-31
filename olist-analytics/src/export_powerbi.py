"""Export a small star schema for Power BI to dashboard/data/*.csv.

    python src/export_powerbi.py

Tables
    fact_orders        one row per valid order (status not canceled/unavailable)
    fact_order_items   one row per item, with category and seller
    dim_customer       one row per customer_unique_id, with cohort month
    dim_seller         one row per seller
Build the date table in Power BI with DAX (see dashboard/README.md).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

QUERIES = {
    "fact_orders": """
        WITH first_item AS (
            SELECT i.order_id, i.seller_id,
                   COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS main_category,
                   ROW_NUMBER() OVER (PARTITION BY i.order_id ORDER BY i.price DESC, i.order_item_id) AS rn
            FROM order_items i
            LEFT JOIN products p ON p.product_id = i.product_id
            LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
        )
        SELECT
            o.order_id,
            c.customer_unique_id,
            c.customer_state,
            date(o.order_purchase_timestamp)                         AS purchase_date,
            o.order_status,
            v.n_items,
            ROUND(v.product_revenue, 2)                              AS product_revenue,
            ROUND(v.freight_revenue, 2)                              AS freight_value,
            fi.seller_id                                             AS main_seller_id,
            fi.main_category,
            CASE WHEN o.order_status = 'delivered'
                  AND o.order_delivered_customer_date IS NOT NULL THEN 1 ELSE 0 END AS is_delivered,
            CASE WHEN o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
                 THEN CASE WHEN date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date)
                           THEN 1 ELSE 0 END END                     AS is_late,
            ROUND(julianday(date(o.order_delivered_customer_date))
                  - julianday(date(o.order_estimated_delivery_date)), 0) AS delay_days,
            ROUND(julianday(o.order_delivered_customer_date)
                  - julianday(o.order_purchase_timestamp), 1)        AS delivery_days,
            ROUND(julianday(o.order_estimated_delivery_date)
                  - julianday(o.order_purchase_timestamp), 1)        AS promised_days,
            r.review_score
        FROM orders o
        JOIN customers c       ON c.customer_id = o.customer_id
        JOIN v_order_value v   ON v.order_id = o.order_id
        LEFT JOIN first_item fi ON fi.order_id = o.order_id AND fi.rn = 1
        LEFT JOIN v_order_reviews r ON r.order_id = o.order_id
        WHERE o.order_status NOT IN ('canceled', 'unavailable')
    """,
    "fact_order_items": """
        SELECT
            i.order_id, i.order_item_id, i.product_id, i.seller_id,
            COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
            date(o.order_purchase_timestamp) AS purchase_date,
            i.price, i.freight_value
        FROM order_items i
        JOIN orders o ON o.order_id = i.order_id
        LEFT JOIN products p ON p.product_id = i.product_id
        LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
        WHERE o.order_status NOT IN ('canceled', 'unavailable')
    """,
    "dim_customer": """
        SELECT
            c.customer_unique_id,
            MAX(c.customer_state)                                       AS customer_state,
            MIN(date(o.order_purchase_timestamp))                       AS first_purchase_date,
            strftime('%Y-%m', MIN(o.order_purchase_timestamp))          AS cohort_month,
            COUNT(DISTINCT o.order_id)                                  AS n_orders
        FROM customers c
        JOIN orders o ON o.customer_id = c.customer_id
        WHERE o.order_status NOT IN ('canceled', 'unavailable')
        GROUP BY c.customer_unique_id
    """,
    "dim_seller": "SELECT seller_id, seller_city, seller_state FROM sellers",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "olist.db")
    ap.add_argument("--out", type=Path, default=ROOT / "dashboard" / "data")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.db)
    for name, sql in QUERIES.items():
        df = pd.read_sql_query(sql, con)
        df.to_csv(args.out / f"{name}.csv", index=False)
        print(f"  {name:<18} {len(df):>9,} rows")
    con.close()
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
