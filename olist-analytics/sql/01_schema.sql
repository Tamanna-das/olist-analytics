-- =====================================================================
-- 01_schema.sql  —  Olist Brazilian E-commerce (SQLite)
-- Creates the nine source tables, indexes and two helper views.
-- Run on a fresh database BEFORE loading data:  python src/etl.py
--
-- Conventions
--   * Timestamps are stored as ISO-8601 TEXT ('YYYY-MM-DD HH:MM:SS'),
--     which SQLite date functions (date(), julianday(), strftime()) read.
--   * Zip code prefixes are TEXT, zero-padded to 5 chars (the CSVs drop
--     leading zeros, e.g. 1037 -> '01037').
--   * Two misspelled source columns are renamed on load:
--       product_name_lenght -> product_name_length
--       product_description_lenght -> product_description_length
-- =====================================================================

PRAGMA foreign_keys = OFF;   -- source data has known orphans; checked in 02

DROP VIEW  IF EXISTS v_order_reviews;
DROP VIEW  IF EXISTS v_order_value;
DROP TABLE IF EXISTS order_reviews;
DROP TABLE IF EXISTS order_payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS category_translation;
DROP TABLE IF EXISTS sellers;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS geolocation;

-- One row per order. NOTE: customer_id is per-order; the person is
-- customer_unique_id. Repeat-purchase analysis must use the latter.
CREATE TABLE customers (
    customer_id               TEXT PRIMARY KEY,
    customer_unique_id        TEXT NOT NULL,
    customer_zip_code_prefix  TEXT,
    customer_city             TEXT,
    customer_state            TEXT
);

-- Many rows per zip prefix (no natural key) -> aggregate before joining.
CREATE TABLE geolocation (
    geolocation_zip_code_prefix TEXT,
    geolocation_lat             REAL,
    geolocation_lng             REAL,
    geolocation_city            TEXT,
    geolocation_state           TEXT
);

CREATE TABLE sellers (
    seller_id               TEXT PRIMARY KEY,
    seller_zip_code_prefix  TEXT,
    seller_city             TEXT,
    seller_state            TEXT
);

CREATE TABLE category_translation (
    product_category_name          TEXT PRIMARY KEY,
    product_category_name_english  TEXT
);

CREATE TABLE products (
    product_id                  TEXT PRIMARY KEY,
    product_category_name       TEXT REFERENCES category_translation(product_category_name),
    product_name_length         INTEGER,
    product_description_length  INTEGER,
    product_photos_qty          INTEGER,
    product_weight_g            REAL,
    product_length_cm           REAL,
    product_height_cm           REAL,
    product_width_cm            REAL
);

CREATE TABLE orders (
    order_id                       TEXT PRIMARY KEY,
    customer_id                    TEXT NOT NULL REFERENCES customers(customer_id),
    order_status                   TEXT NOT NULL,
    order_purchase_timestamp       TEXT NOT NULL,
    order_approved_at              TEXT,
    order_delivered_carrier_date   TEXT,   -- POST-ORDER: never a model feature
    order_delivered_customer_date  TEXT,   -- POST-ORDER: never a model feature
    order_estimated_delivery_date  TEXT    -- shown at checkout: allowed
);

CREATE TABLE order_items (
    order_id             TEXT NOT NULL REFERENCES orders(order_id),
    order_item_id        INTEGER NOT NULL,       -- 1..n within an order
    product_id           TEXT NOT NULL REFERENCES products(product_id),
    seller_id            TEXT NOT NULL REFERENCES sellers(seller_id),
    shipping_limit_date  TEXT,
    price                REAL NOT NULL,
    freight_value        REAL NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE order_payments (
    order_id              TEXT NOT NULL REFERENCES orders(order_id),
    payment_sequential    INTEGER NOT NULL,
    payment_type          TEXT,
    payment_installments  INTEGER,
    payment_value         REAL,
    PRIMARY KEY (order_id, payment_sequential)
);

-- review_id is NOT unique in the source, and some orders have >1 review,
-- so no primary key here. Use v_order_reviews for one review per order.
CREATE TABLE order_reviews (
    review_id                TEXT NOT NULL,
    order_id                 TEXT NOT NULL REFERENCES orders(order_id),
    review_score             INTEGER,
    review_comment_title     TEXT,
    review_comment_message   TEXT,
    review_creation_date     TEXT,
    review_answer_timestamp  TEXT
);

-- ---------------------------------------------------------------------
-- Indexes for the joins used in 02-06
-- ---------------------------------------------------------------------
CREATE INDEX ix_customers_unique   ON customers(customer_unique_id);
CREATE INDEX ix_geo_zip            ON geolocation(geolocation_zip_code_prefix);
CREATE INDEX ix_orders_customer    ON orders(customer_id);
CREATE INDEX ix_orders_purchase    ON orders(order_purchase_timestamp);
CREATE INDEX ix_items_product      ON order_items(product_id);
CREATE INDEX ix_items_seller       ON order_items(seller_id);
CREATE INDEX ix_reviews_order      ON order_reviews(order_id);

-- ---------------------------------------------------------------------
-- Helper views (cheap, reused by 03-06)
-- ---------------------------------------------------------------------

-- Order-level value from items: product revenue, freight, item count.
CREATE VIEW v_order_value AS
SELECT
    order_id,
    COUNT(*)                      AS n_items,
    SUM(price)                    AS product_revenue,
    SUM(freight_value)            AS freight_revenue,
    SUM(price + freight_value)    AS gross_order_value
FROM order_items
GROUP BY order_id;

-- Latest review per order (dedupes the multi-review orders).
CREATE VIEW v_order_reviews AS
WITH ranked AS (
    SELECT
        r.*,
        ROW_NUMBER() OVER (
            PARTITION BY r.order_id
            ORDER BY r.review_answer_timestamp DESC, r.review_creation_date DESC, r.review_id
        ) AS rn
    FROM order_reviews r
)
SELECT order_id, review_id, review_score, review_creation_date, review_answer_timestamp
FROM ranked
WHERE rn = 1;
