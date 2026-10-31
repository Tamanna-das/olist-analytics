"""Build the order-level modelling table for late-delivery prediction.

Target
    is_late = 1 if the order was delivered on a later calendar date than
    order_estimated_delivery_date (same definition as sql/06).

Leakage rule
    Every feature must be known at the moment of purchase. Allowed:
    basket contents, prices, freight, product size, seller/customer
    location, payment chosen at checkout, the delivery estimate shown at
    checkout, and seller history from orders that had ALREADY been
    delivered before this order was placed.
    Never used: order_approved_at, order_delivered_carrier_date,
    order_delivered_customer_date, order_status, any review field.

Usage:
    python src/features.py            # data/olist.db -> data/features.csv
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

NUMERIC_FEATURES = [
    "promised_days",
    "purchase_hour",
    "purchase_dow",
    "is_weekend",
    "n_items",
    "n_products",
    "n_sellers",
    "total_price",
    "total_freight",
    "freight_ratio",
    "max_item_price",
    "total_weight_kg",
    "max_weight_kg",
    "total_volume_l",
    "avg_photos",
    "distance_km",
    "same_state",
    "customer_lat",
    "customer_lng",
    "payment_installments",
    "n_payment_types",
    "seller_prior_orders",
    "seller_prior_delivered",
    "seller_prior_late",
    "seller_days_active",
    "route_prior_delivered",
    "route_prior_late",
    # recent (rolling-window) delivery history, known at purchase time
    "route_recent_delivered",
    "route_recent_late",
    "route_recent_mean_days",
    "network_recent_late_rate",
    "network_recent_mean_days",
    # slack: how generous is the promise vs how fast this lane is right now?
    "slack_days",
    "slack_ratio",
]
# Purchase month is deliberately NOT a feature: with ~1.5 years of history
# it mostly memorises which months had disruptions.
ROUTE_WINDOW_DAYS = 60
NETWORK_WINDOW_DAYS = 14
MIN_ROUTE_RECENT = 10   # below this, fall back to the network-wide mean
CATEGORICAL_FEATURES = [
    "customer_state",
    "seller_state",
    "main_category",
    "payment_type",
]
# Raw history counts are turned into smoothed rates in train.py, using a
# prior computed on the training split only.
HISTORY_PAIRS = {
    "seller_prior_late_rate": ("seller_prior_late", "seller_prior_delivered"),
    "route_prior_late_rate": ("route_prior_late", "route_prior_delivered"),
    "route_recent_late_rate": ("route_recent_late", "route_recent_delivered"),
}

BRAZIL_LAT = (-34.0, 6.0)
BRAZIL_LNG = (-74.0, -34.0)


def haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def read(con, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, con)


def prior_late_stats(events: pd.DataFrame, queries: pd.DataFrame, key: str, prefix: str) -> pd.DataFrame:
    """Point-in-time history: for each query row (key, purchase_ts), count the
    key's orders DELIVERED strictly before purchase_ts, and how many were late.

    events:  [key, delivered_ts, is_late]   one row per delivered order
    queries: [order_id, key, purchase_ts]
    """
    ev = events.sort_values("delivered_ts").copy()
    ev[f"{prefix}_prior_delivered"] = ev.groupby(key).cumcount() + 1
    ev[f"{prefix}_prior_late"] = ev.groupby(key)["is_late"].cumsum()
    q = queries.sort_values("purchase_ts")
    merged = pd.merge_asof(
        q,
        ev[[key, "delivered_ts", f"{prefix}_prior_delivered", f"{prefix}_prior_late"]],
        left_on="purchase_ts",
        right_on="delivered_ts",
        by=key,
        direction="backward",
        allow_exact_matches=False,
    )
    cols = [f"{prefix}_prior_delivered", f"{prefix}_prior_late"]
    merged[cols] = merged[cols].fillna(0)
    return merged[["order_id", *cols]]


def recent_window_stats(events: pd.DataFrame, queries: pd.DataFrame, key: str, window_days: int,
                        prefix: str) -> pd.DataFrame:
    """Rolling point-in-time history: for each query (key, purchase_ts), use the
    key's orders DELIVERED in [purchase_ts - window, purchase_ts).

    events:  [key, delivered_ts, is_late, delivery_days]
    queries: [order_id, key, purchase_ts]
    Returns  order_id, <prefix>_recent_delivered, <prefix>_recent_late,
             <prefix>_recent_days_sum
    """
    ev = events.sort_values("delivered_ts").copy()
    g = ev.groupby(key)
    ev["c_n"] = g.cumcount() + 1
    ev["c_late"] = g["is_late"].cumsum()
    ev["c_days"] = g["delivery_days"].cumsum()
    right = ev[[key, "delivered_ts", "c_n", "c_late", "c_days"]]
    cols = ["c_n", "c_late", "c_days"]

    def cum_at(times: pd.Series) -> pd.DataFrame:
        q = pd.DataFrame({"order_id": queries.order_id.values, key: queries[key].values, "t": times.values})
        q = q.sort_values("t")
        m = pd.merge_asof(q, right, left_on="t", right_on="delivered_ts", by=key,
                          direction="backward", allow_exact_matches=False)
        m[cols] = m[cols].fillna(0)
        return m.set_index("order_id")[cols]

    now = cum_at(queries.purchase_ts)
    before = cum_at(queries.purchase_ts - pd.Timedelta(days=window_days))
    diff = (now - before.reindex(now.index)).reset_index()
    return diff.rename(columns={"c_n": f"{prefix}_recent_delivered", "c_late": f"{prefix}_recent_late",
                                "c_days": f"{prefix}_recent_days_sum"})


def build(db_path: Path) -> pd.DataFrame:
    con = sqlite3.connect(db_path)

    orders = read(con, """
        SELECT o.order_id, o.order_status, o.order_purchase_timestamp AS purchase_ts,
               o.order_estimated_delivery_date AS estimated_ts,
               o.order_delivered_customer_date AS delivered_ts,
               c.customer_state, c.customer_zip_code_prefix AS customer_zip
        FROM orders o JOIN customers c ON c.customer_id = o.customer_id
        WHERE o.order_status NOT IN ('canceled', 'unavailable')
    """)
    for c in ("purchase_ts", "estimated_ts", "delivered_ts"):
        orders[c] = pd.to_datetime(orders[c])

    items = read(con, """
        SELECT i.order_id, i.order_item_id, i.product_id, i.seller_id, i.price, i.freight_value,
               s.seller_state, s.seller_zip_code_prefix AS seller_zip,
               COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
               p.product_weight_g, p.product_length_cm, p.product_height_cm, p.product_width_cm,
               p.product_photos_qty
        FROM order_items i
        LEFT JOIN sellers s  ON s.seller_id = i.seller_id
        LEFT JOIN products p ON p.product_id = i.product_id
        LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
    """)
    payments = read(con, "SELECT order_id, payment_type, payment_installments, payment_value FROM order_payments")
    geo = read(con, f"""
        SELECT geolocation_zip_code_prefix AS zip, AVG(geolocation_lat) AS lat, AVG(geolocation_lng) AS lng
        FROM geolocation
        WHERE geolocation_lat BETWEEN {BRAZIL_LAT[0]} AND {BRAZIL_LAT[1]}
          AND geolocation_lng BETWEEN {BRAZIL_LNG[0]} AND {BRAZIL_LNG[1]}
        GROUP BY geolocation_zip_code_prefix
    """)
    con.close()

    # ---- basket features -------------------------------------------------
    items["volume_l"] = items.product_length_cm * items.product_height_cm * items.product_width_cm / 1000
    items["weight_kg"] = items.product_weight_g / 1000
    basket = items.groupby("order_id").agg(
        n_items=("order_item_id", "count"),
        n_products=("product_id", "nunique"),
        n_sellers=("seller_id", "nunique"),
        total_price=("price", "sum"),
        total_freight=("freight_value", "sum"),
        max_item_price=("price", "max"),
        total_weight_kg=("weight_kg", "sum"),
        max_weight_kg=("weight_kg", "max"),
        total_volume_l=("volume_l", "sum"),
        avg_photos=("product_photos_qty", "mean"),
    )
    basket["freight_ratio"] = basket.total_freight / (basket.total_price + basket.total_freight)

    # main item = most expensive item -> its seller and category
    main = (items.sort_values(["order_id", "price", "order_item_id"], ascending=[True, False, True])
                 .drop_duplicates("order_id")
                 .set_index("order_id")[["seller_id", "seller_state", "seller_zip", "category"]]
                 .rename(columns={"category": "main_category"}))

    # ---- payment chosen at checkout ------------------------------------
    pay_main = (payments.sort_values(["order_id", "payment_value"], ascending=[True, False])
                        .drop_duplicates("order_id").set_index("order_id")[["payment_type"]])
    pay_agg = payments.groupby("order_id").agg(
        payment_installments=("payment_installments", "max"),
        n_payment_types=("payment_type", "nunique"),
    )

    df = (orders.set_index("order_id")
                .join(basket, how="inner")
                .join(main)
                .join(pay_main)
                .join(pay_agg)
                .reset_index())

    # ---- geography -------------------------------------------------------
    g = geo.set_index("zip")
    df["customer_lat"] = df.customer_zip.map(g.lat)
    df["customer_lng"] = df.customer_zip.map(g.lng)
    seller_lat = df.seller_zip.map(g.lat)
    seller_lng = df.seller_zip.map(g.lng)
    df["distance_km"] = haversine_km(df.customer_lat, df.customer_lng, seller_lat, seller_lng)
    df["same_state"] = (df.customer_state == df.seller_state).astype(int)

    # ---- time at purchase + the promise shown at checkout ---------------
    df["promised_days"] = (df.estimated_ts - df.purchase_ts).dt.total_seconds() / 86400
    df["purchase_hour"] = df.purchase_ts.dt.hour
    df["purchase_dow"] = df.purchase_ts.dt.dayofweek
    df["purchase_month"] = df.purchase_ts.dt.month
    df["is_weekend"] = (df.purchase_dow >= 5).astype(int)

    # ---- target (delivered orders only) ---------------------------------
    delivered = (df.order_status == "delivered") & df.delivered_ts.notna() & df.estimated_ts.notna()
    df["is_late"] = np.where(
        delivered, (df.delivered_ts.dt.normalize() > df.estimated_ts.dt.normalize()).astype(float), np.nan
    )

    # ---- point-in-time seller and route history -------------------------
    # Seller order count before this purchase (any status except canceled).
    df = df.sort_values("purchase_ts")
    df["seller_prior_orders"] = df.groupby("seller_id").cumcount()
    first_seen = df.groupby("seller_id").purchase_ts.transform("min")
    df["seller_days_active"] = (df.purchase_ts - first_seen).dt.total_seconds() / 86400

    # Delivery outcomes become known on the delivery date, not the purchase date.
    done = df.loc[delivered, ["order_id", "seller_id", "seller_state", "customer_state",
                              "purchase_ts", "delivered_ts", "is_late"]]
    done = done.assign(delivery_days=(done.delivered_ts - done.purchase_ts).dt.total_seconds() / 86400)
    seller_hist = prior_late_stats(
        done[["seller_id", "delivered_ts", "is_late"]],
        df[["order_id", "seller_id", "purchase_ts"]],
        key="seller_id", prefix="seller",
    )
    df["route"] = df.seller_state.fillna("NA") + "->" + df.customer_state.fillna("NA")
    done = done.assign(route=done.seller_state.fillna("NA") + "->" + done.customer_state.fillna("NA"))
    route_hist = prior_late_stats(
        done[["route", "delivered_ts", "is_late"]],
        df[["order_id", "route", "purchase_ts"]],
        key="route", prefix="route",
    )
    df = df.merge(seller_hist, on="order_id").merge(route_hist, on="order_id")

    # Recent speed and lateness of this lane and of the whole network.
    route_recent = recent_window_stats(
        done[["route", "delivered_ts", "is_late", "delivery_days"]],
        df[["order_id", "route", "purchase_ts"]],
        key="route", window_days=ROUTE_WINDOW_DAYS, prefix="route",
    )
    net_recent = recent_window_stats(
        done.assign(all="all")[["all", "delivered_ts", "is_late", "delivery_days"]],
        df.assign(all="all")[["order_id", "all", "purchase_ts"]],
        key="all", window_days=NETWORK_WINDOW_DAYS, prefix="network",
    )
    df = df.merge(route_recent, on="order_id").merge(net_recent, on="order_id")
    n_net = df.network_recent_delivered.replace(0, np.nan)
    df["network_recent_late_rate"] = df.network_recent_late / n_net
    df["network_recent_mean_days"] = df.network_recent_days_sum / n_net
    route_mean = df.route_recent_days_sum / df.route_recent_delivered.replace(0, np.nan)
    df["route_recent_mean_days"] = route_mean.where(df.route_recent_delivered >= MIN_ROUTE_RECENT,
                                                    df.network_recent_mean_days)
    df["slack_days"] = df.promised_days - df.route_recent_mean_days
    df["slack_ratio"] = df.promised_days / df.route_recent_mean_days

    keep = ["order_id", "purchase_ts", "is_late", *NUMERIC_FEATURES, *CATEGORICAL_FEATURES]
    out = df.loc[df.is_late.notna(), keep].sort_values("purchase_ts").reset_index(drop=True)
    out["is_late"] = out.is_late.astype(int)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "olist.db")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "features.csv")
    args = ap.parse_args()
    out = build(args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"{len(out):,} delivered orders, late rate {out.is_late.mean():.2%} -> {args.out}")


if __name__ == "__main__":
    main()
