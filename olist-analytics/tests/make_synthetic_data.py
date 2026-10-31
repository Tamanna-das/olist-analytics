"""Generate SYNTHETIC Olist-shaped CSVs for smoke-testing the pipeline.

The files have the real Olist file names, column names (including the
misspelled 'lenght' columns), and quirks (per-order customer_id, int zip
codes without leading zeros, duplicate geolocation rows, multi-review
orders, a BOM in the translation file, sparse first/last months).
The numbers are fake — never report results computed from them.

    python tests/make_synthetic_data.py --out data/synthetic --orders 20000
"""
from __future__ import annotations

import argparse
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

STATES = {  # state: (lat, lng, weight)
    "SP": (-23.5, -46.6, 0.42), "RJ": (-22.9, -43.2, 0.13), "MG": (-19.9, -43.9, 0.12),
    "RS": (-30.0, -51.2, 0.055), "PR": (-25.4, -49.3, 0.05), "SC": (-27.6, -48.5, 0.037),
    "BA": (-12.97, -38.5, 0.034), "DF": (-15.8, -47.9, 0.021), "GO": (-16.7, -49.3, 0.02),
    "ES": (-20.3, -40.3, 0.02), "PE": (-8.05, -34.9, 0.017), "CE": (-3.7, -38.5, 0.013),
    "PA": (-1.45, -48.5, 0.01), "AM": (-3.1, -60.0, 0.006), "MA": (-2.5, -44.3, 0.006),
}
CATEGORIES = {
    "cama_mesa_banho": "bed_bath_table", "beleza_saude": "health_beauty", "esporte_lazer": "sports_leisure",
    "moveis_decoracao": "furniture_decor", "informatica_acessorios": "computers_accessories",
    "utilidades_domesticas": "housewares", "relogios_presentes": "watches_gifts", "telefonia": "telephony",
    "ferramentas_jardim": "garden_tools", "automotivo": "auto", "brinquedos": "toys",
    "cool_stuff": "cool_stuff", "perfumaria": "perfumery", "bebes": "baby", "eletronicos": "electronics",
    "papelaria": "stationery", "fashion_bolsas_e_acessorios": "fashion_bags_accessories",
    "pet_shop": "pet_shop", "moveis_escritorio": "office_furniture", "pc_gamer": None,  # untranslated
}


def uid() -> str:
    return uuid.UUID(int=int(RNG.integers(0, 2**63)) << 64 | int(RNG.integers(0, 2**63))).hex


def zips_for(state: str, n: int, base: int) -> list[int]:
    return list(base + RNG.choice(np.arange(0, 900), size=n, replace=False))


def main(out: Path, n_orders: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    states = list(STATES)
    sw = np.array([STATES[s][2] for s in states]); sw /= sw.sum()

    # ---- geography: each state gets a block of zip prefixes ------------
    zip_state, geo_rows = {}, []
    for k, s in enumerate(states):
        base = 1000 + k * 6000          # SP starts at 01000 -> leading zero in real data
        for z in zips_for(s, 60, base):
            zip_state[z] = s
            lat0, lng0 = STATES[s][:2]
            for _ in range(int(RNG.integers(1, 4))):   # duplicate rows per prefix
                geo_rows.append((z, lat0 + RNG.normal(0, 0.6), lng0 + RNG.normal(0, 0.6), f"city_{z}", s))
    geo_rows.append((1001, 42.0, -8.0, "outlier", "SP"))   # bad coordinate outside Brazil
    geo = pd.DataFrame(geo_rows, columns=["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
                                          "geolocation_city", "geolocation_state"])
    zips_by_state = {s: [z for z, st in zip_state.items() if st == s] for s in states}

    # ---- sellers, products ---------------------------------------------
    n_sellers = 400
    seller_state = RNG.choice(states, n_sellers, p=np.where(np.array(states) == "SP", 4, 1) * sw / (np.where(np.array(states) == "SP", 4, 1) * sw).sum())
    sellers = pd.DataFrame({
        "seller_id": [uid() for _ in range(n_sellers)],
        "seller_zip_code_prefix": [RNG.choice(zips_by_state[s]) for s in seller_state],
        "seller_city": "x", "seller_state": seller_state,
    })
    seller_quality = RNG.normal(0, 0.6, n_sellers)            # latent lateness propensity
    seller_pop = RNG.pareto(1.2, n_sellers) + 0.05; seller_pop /= seller_pop.sum()

    n_products = 3000
    cat_names = list(CATEGORIES)
    cat_w = RNG.dirichlet(np.ones(len(cat_names)) * 0.8)
    products = pd.DataFrame({
        "product_id": [uid() for _ in range(n_products)],
        "product_category_name": RNG.choice(cat_names, n_products, p=cat_w),
        "product_name_lenght": RNG.integers(10, 70, n_products),
        "product_description_lenght": RNG.integers(50, 3000, n_products),
        "product_photos_qty": RNG.integers(1, 8, n_products),
        "product_weight_g": np.round(RNG.lognormal(6.5, 1.1, n_products)),
        "product_length_cm": RNG.integers(10, 80, n_products),
        "product_height_cm": RNG.integers(2, 60, n_products),
        "product_width_cm": RNG.integers(8, 60, n_products),
    })
    products.loc[RNG.choice(n_products, 30, replace=False), "product_category_name"] = np.nan
    product_seller = RNG.choice(n_sellers, n_products, p=seller_pop)
    product_price = np.round(RNG.lognormal(4.3, 0.9, n_products), 2)

    # ---- order timeline: sparse start, growth, sparse tail -------------
    start, end = pd.Timestamp("2016-10-01"), pd.Timestamp("2018-09-10")
    days = (end - start).days
    t = RNG.beta(1.6, 1.0, n_orders) * days
    purchase = (start + pd.to_timedelta(t, unit="D") + pd.to_timedelta(RNG.integers(0, 86400, n_orders), unit="s")).sort_values()

    # ---- customers: ~3% of people repeat ---------------------------------
    n_people = int(n_orders * 0.97)
    people = [uid() for _ in range(n_people)]
    person_state = RNG.choice(states, n_people, p=sw)
    person_zip = [RNG.choice(zips_by_state[s]) for s in person_state]
    person_of_order = np.concatenate([np.arange(n_people), RNG.integers(0, n_people, n_orders - n_people)])
    RNG.shuffle(person_of_order)

    orders, customers, items, payments, reviews = [], [], [], [], []
    for k in range(n_orders):
        p = person_of_order[k]
        cid, oid = uid(), uid()
        customers.append((cid, people[p], person_zip[p], "city", person_state[p]))
        pt = purchase[k]
        n_it = 1 + RNG.binomial(3, 0.06)
        prods = RNG.choice(n_products, n_it)
        sel = product_seller[prods[0]]
        cust_ll = np.array(STATES[person_state[p]][:2]); sell_ll = np.array(STATES[seller_state[sel]][:2])
        dist = np.linalg.norm(cust_ll - sell_ll) * 111
        promised = 8 + dist / 120 + RNG.normal(0, 3)
        promised = max(promised, 3)
        est = (pt + pd.Timedelta(days=promised)).normalize()

        # true delivery time
        strike = 1.0 if pd.Timestamp("2018-02-01") <= pt < pd.Timestamp("2018-04-01") else 0.0
        base_days = 4 + dist / 180 + RNG.gamma(2, 2)
        logit = -2.6 + 0.9 * seller_quality[sel] + 1.1 * strike + 0.4 * (dist > 1500) + 0.25 * n_it - 0.08 * (promised - 12)
        late = RNG.random() < 1 / (1 + np.exp(-logit))
        actual = promised + RNG.uniform(1, 12) if late else min(base_days, promised - RNG.uniform(0, 8))
        actual = max(actual, 1.0)
        approved = pt + pd.Timedelta(hours=float(RNG.gamma(1.2, 8)))
        carrier = approved + pd.Timedelta(days=float(min(actual * 0.3, RNG.gamma(2, 1.2))))
        delivered = pt + pd.Timedelta(days=float(actual))
        if delivered <= carrier:
            delivered = carrier + pd.Timedelta(hours=6)

        r = RNG.random()
        status = "delivered"
        if pt > pd.Timestamp("2018-08-25") or delivered > end + pd.Timedelta(days=30):
            status = "shipped"
        if r < 0.006: status = "canceled"
        elif r < 0.012: status = "unavailable"
        elif r < 0.016: status = "shipped"
        elif r < 0.018: status = "invoiced"
        fmt = lambda x: x.strftime("%Y-%m-%d %H:%M:%S")
        orders.append((oid, cid, status, fmt(pt),
                       fmt(approved) if status != "canceled" else "",
                       fmt(carrier) if status in ("delivered", "shipped") else "",
                       fmt(delivered) if status == "delivered" else "",
                       fmt(est)))
        total = 0.0
        for j, pr in enumerate(prods, start=1):
            price = product_price[pr]; freight = round(8 + dist / 200 + RNG.gamma(2, 3), 2)
            items.append((oid, j, products.product_id[pr], sellers.seller_id[product_seller[pr]],
                          fmt(pt + pd.Timedelta(days=6)), price, freight))
            total += price + freight
        if RNG.random() < 0.03:   # voucher + card
            payments.append((oid, 1, "voucher", 1, round(total * 0.3, 2)))
            payments.append((oid, 2, "credit_card", int(RNG.integers(1, 10)), round(total * 0.7, 2)))
        else:
            ptype = RNG.choice(["credit_card", "boleto", "debit_card"], p=[0.76, 0.21, 0.03])
            payments.append((oid, 1, ptype, int(RNG.integers(1, 10)) if ptype == "credit_card" else 1, round(total, 2)))
        if status == "delivered" or RNG.random() < 0.5:
            mu = 4.4 - (2.4 if (status == "delivered" and late) else 0) - (1.5 if status != "delivered" else 0)
            score = int(np.clip(np.round(RNG.normal(mu, 1.0)), 1, 5))
            rid = uid()
            reviews.append((rid, oid, score, "", "", fmt(delivered.normalize() + pd.Timedelta(days=1)),
                            fmt(delivered + pd.Timedelta(days=2))))
            if RNG.random() < 0.007:   # duplicate review / multi-review quirk
                reviews.append((rid if RNG.random() < 0.5 else uid(), oid, max(1, score - 1), "", "",
                                fmt(delivered.normalize() + pd.Timedelta(days=3)), fmt(delivered + pd.Timedelta(days=4))))

    pd.DataFrame(customers, columns=["customer_id", "customer_unique_id", "customer_zip_code_prefix",
                                     "customer_city", "customer_state"]).to_csv(out / "olist_customers_dataset.csv", index=False)
    geo.to_csv(out / "olist_geolocation_dataset.csv", index=False)
    sellers.to_csv(out / "olist_sellers_dataset.csv", index=False)
    pd.DataFrame([(k, v) for k, v in CATEGORIES.items() if v],
                 columns=["product_category_name", "product_category_name_english"]
                 ).to_csv(out / "product_category_name_translation.csv", index=False, encoding="utf-8-sig")
    products.to_csv(out / "olist_products_dataset.csv", index=False)
    pd.DataFrame(orders, columns=["order_id", "customer_id", "order_status", "order_purchase_timestamp",
                                  "order_approved_at", "order_delivered_carrier_date",
                                  "order_delivered_customer_date", "order_estimated_delivery_date"]
                 ).to_csv(out / "olist_orders_dataset.csv", index=False)
    pd.DataFrame(items, columns=["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date",
                                 "price", "freight_value"]).to_csv(out / "olist_order_items_dataset.csv", index=False)
    pd.DataFrame(payments, columns=["order_id", "payment_sequential", "payment_type", "payment_installments",
                                    "payment_value"]).to_csv(out / "olist_order_payments_dataset.csv", index=False)
    pd.DataFrame(reviews, columns=["review_id", "order_id", "review_score", "review_comment_title",
                                   "review_comment_message", "review_creation_date", "review_answer_timestamp"]
                 ).to_csv(out / "olist_order_reviews_dataset.csv", index=False)
    print(f"Synthetic Olist-shaped data ({n_orders:,} orders) -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "synthetic")
    ap.add_argument("--orders", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    RNG = np.random.default_rng(a.seed)
    main(a.out, a.orders)
