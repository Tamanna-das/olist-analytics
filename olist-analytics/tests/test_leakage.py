"""Leakage checks for the feature table.

    python tests/test_leakage.py --db data/olist.db --features data/features.csv

1. No post-purchase columns appear among the features.
2. For a random sample of orders, seller history is recomputed by brute
   force from the database and must match: only orders DELIVERED strictly
   before this order's purchase time may count.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from features import CATEGORICAL_FEATURES, NUMERIC_FEATURES  # noqa: E402

FORBIDDEN = ("approved", "carrier", "delivered_customer", "review", "order_status", "delay", "actual")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "olist.db")
    ap.add_argument("--features", type=Path, default=ROOT / "data" / "features.csv")
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()

    bad = [f for f in NUMERIC_FEATURES + CATEGORICAL_FEATURES
           if any(tok in f for tok in FORBIDDEN) and not f.startswith(("seller_prior", "route_prior"))]
    assert not bad, f"post-purchase fields used as features: {bad}"

    feats = pd.read_csv(a.features, parse_dates=["purchase_ts"])
    con = sqlite3.connect(a.db)
    main_seller = pd.read_sql_query("""
        SELECT order_id, seller_id FROM (
            SELECT order_id, seller_id,
                   ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY price DESC, order_item_id) AS rn
            FROM order_items) WHERE rn = 1""", con).set_index("order_id").seller_id
    seller_orders = pd.read_sql_query("""
        SELECT DISTINCT i.seller_id, o.order_id, o.order_purchase_timestamp AS p,
               o.order_delivered_customer_date AS d, o.order_estimated_delivery_date AS e
        FROM order_items i JOIN orders o ON o.order_id = i.order_id
        WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
          AND o.order_estimated_delivery_date IS NOT NULL""", con, parse_dates=["p", "d", "e"])
    con.close()
    # features.py attributes history to each order's MAIN seller only
    seller_orders = seller_orders[seller_orders.order_id.map(main_seller) == seller_orders.seller_id]

    sample = feats.sample(min(a.n, len(feats)), random_state=0)
    mismatches = 0
    for row in sample.itertuples():
        s = main_seller[row.order_id]
        hist = seller_orders[(seller_orders.seller_id == s) & (seller_orders.d < row.purchase_ts)]
        n, late = len(hist), int((hist.d.dt.normalize() > hist.e.dt.normalize()).sum())
        if (n, late) != (int(row.seller_prior_delivered), int(row.seller_prior_late)):
            mismatches += 1
            if mismatches <= 5:
                print(f"  mismatch {row.order_id}: expected ({n},{late}) got "
                      f"({row.seller_prior_delivered},{row.seller_prior_late})")
    assert mismatches == 0, f"{mismatches}/{len(sample)} seller-history mismatches"

    # Rolling route window: deliveries in [purchase - W, purchase) on the same lane.
    from features import ROUTE_WINDOW_DAYS
    con = sqlite3.connect(a.db)
    lane = pd.read_sql_query("""
        SELECT o.order_id, s.seller_state || '->' || c.customer_state AS route,
               o.order_purchase_timestamp AS p, o.order_delivered_customer_date AS d,
               o.order_estimated_delivery_date AS e, o.order_status AS st
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        JOIN (SELECT order_id, seller_id FROM (
                SELECT order_id, seller_id,
                       ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY price DESC, order_item_id) AS rn
                FROM order_items) WHERE rn = 1) m ON m.order_id = o.order_id
        JOIN sellers s ON s.seller_id = m.seller_id
        WHERE o.order_status NOT IN ('canceled', 'unavailable')""", con, parse_dates=["p", "d", "e"])
    con.close()
    lane_of = lane.set_index("order_id").route
    done = lane[(lane.st == "delivered") & lane.d.notna() & lane.e.notna()]
    window = pd.Timedelta(days=ROUTE_WINDOW_DAYS)
    bad = 0
    for row in sample.itertuples():
        h = done[(done.route == lane_of[row.order_id]) & (done.d < row.purchase_ts)
                 & (done.d >= row.purchase_ts - window)]
        exp = (len(h), int((h.d.dt.normalize() > h.e.dt.normalize()).sum()))
        if exp != (int(row.route_recent_delivered), int(row.route_recent_late)):
            bad += 1
            if bad <= 5:
                print(f"  route mismatch {row.order_id}: expected {exp} got "
                      f"({row.route_recent_delivered},{row.route_recent_late})")
    assert bad == 0, f"{bad}/{len(sample)} route-window mismatches"
    print(f"OK: no post-purchase features; seller history and {ROUTE_WINDOW_DAYS}-day route window "
          f"verified on {len(sample)} orders")


if __name__ == "__main__":
    main()
