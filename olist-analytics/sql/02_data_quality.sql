-- =====================================================================
-- 02_data_quality.sql
-- Each query is tagged "-- @name <id>"; src/run_sql.py saves each result
-- to reports/sql_outputs/<id>.csv. Every query should return quickly and
-- is read-only.
-- =====================================================================

-- @name dq_row_counts
-- Row counts per table (record these in the README).
SELECT 'customers'            AS table_name, COUNT(*) AS n_rows FROM customers
UNION ALL SELECT 'geolocation',          COUNT(*) FROM geolocation
UNION ALL SELECT 'sellers',              COUNT(*) FROM sellers
UNION ALL SELECT 'category_translation', COUNT(*) FROM category_translation
UNION ALL SELECT 'products',             COUNT(*) FROM products
UNION ALL SELECT 'orders',               COUNT(*) FROM orders
UNION ALL SELECT 'order_items',          COUNT(*) FROM order_items
UNION ALL SELECT 'order_payments',       COUNT(*) FROM order_payments
UNION ALL SELECT 'order_reviews',        COUNT(*) FROM order_reviews;

-- @name dq_customer_identity
-- customer_id is minted per order; customer_unique_id is the person.
-- If n_customer_ids >> n_unique_customers, using customer_id would make
-- almost everyone look like a one-time buyer.
WITH per_person AS (
    SELECT c.customer_unique_id, COUNT(DISTINCT o.order_id) AS n_orders
    FROM customers c
    JOIN orders o ON o.customer_id = c.customer_id
    GROUP BY c.customer_unique_id
)
SELECT
    (SELECT COUNT(DISTINCT customer_id)        FROM customers) AS n_customer_ids,
    (SELECT COUNT(DISTINCT customer_unique_id) FROM customers) AS n_unique_customers,
    (SELECT COUNT(*) FROM per_person WHERE n_orders >= 2)      AS n_people_with_2plus_orders,
    (SELECT MAX(n_orders) FROM per_person)                     AS max_orders_one_person,
    (SELECT COUNT(*) FROM (SELECT customer_id FROM orders
                           GROUP BY customer_id HAVING COUNT(*) > 1)) AS customer_ids_with_2plus_orders;

-- @name dq_order_status
SELECT
    order_status,
    COUNT(*)                                            AS n_orders,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)  AS pct
FROM orders
GROUP BY order_status
ORDER BY n_orders DESC;

-- @name dq_null_timestamps_by_status
-- Missing lifecycle timestamps, by status. Delivered orders should have
-- all of them; a few in the real data do not.
SELECT
    order_status,
    COUNT(*)                                                         AS n_orders,
    SUM(order_approved_at IS NULL)                                   AS null_approved,
    SUM(order_delivered_carrier_date IS NULL)                        AS null_carrier,
    SUM(order_delivered_customer_date IS NULL)                       AS null_delivered,
    SUM(order_estimated_delivery_date IS NULL)                       AS null_estimate
FROM orders
GROUP BY order_status
ORDER BY n_orders DESC;

-- @name dq_timestamp_order_violations
-- Lifecycle events out of sequence.
SELECT
    SUM(order_approved_at < order_purchase_timestamp)                     AS approved_before_purchase,
    SUM(order_delivered_carrier_date < order_approved_at)                 AS carrier_before_approval,
    SUM(order_delivered_customer_date < order_delivered_carrier_date)     AS delivered_before_carrier,
    SUM(order_delivered_customer_date < order_purchase_timestamp)         AS delivered_before_purchase,
    SUM(order_status = 'delivered' AND order_delivered_customer_date IS NULL) AS delivered_status_no_date,
    SUM(order_status <> 'delivered' AND order_delivered_customer_date IS NOT NULL) AS date_but_not_delivered
FROM orders;

-- @name dq_purchase_date_coverage
-- Orders per month. Olist's first (2016) and last (Sep-Oct 2018) months
-- are sparse; 03-06 restrict trend analysis to well-covered months.
SELECT
    strftime('%Y-%m', order_purchase_timestamp) AS purchase_month,
    COUNT(*)                                    AS n_orders
FROM orders
GROUP BY purchase_month
ORDER BY purchase_month;

-- @name dq_duplicates
SELECT 'orders.order_id'              AS check_name,
       COUNT(*) - COUNT(DISTINCT order_id) AS n_duplicates FROM orders
UNION ALL
SELECT 'order_reviews.review_id',
       COUNT(*) - COUNT(DISTINCT review_id) FROM order_reviews
UNION ALL
SELECT 'orders with >1 review',
       COUNT(*) FROM (SELECT order_id FROM order_reviews GROUP BY order_id HAVING COUNT(*) > 1)
UNION ALL
SELECT 'geolocation rows per zip (excess)',
       COUNT(*) - COUNT(DISTINCT geolocation_zip_code_prefix) FROM geolocation;

-- @name dq_orphans
-- Referential integrity (foreign keys are not enforced on load).
SELECT 'items -> missing product' AS check_name, COUNT(*) AS n
FROM order_items i LEFT JOIN products p ON p.product_id = i.product_id WHERE p.product_id IS NULL
UNION ALL
SELECT 'items -> missing seller', COUNT(*)
FROM order_items i LEFT JOIN sellers s ON s.seller_id = i.seller_id WHERE s.seller_id IS NULL
UNION ALL
SELECT 'items -> missing order', COUNT(*)
FROM order_items i LEFT JOIN orders o ON o.order_id = i.order_id WHERE o.order_id IS NULL
UNION ALL
SELECT 'orders -> missing customer', COUNT(*)
FROM orders o LEFT JOIN customers c ON c.customer_id = o.customer_id WHERE c.customer_id IS NULL
UNION ALL
SELECT 'orders with no items', COUNT(*)
FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.order_id)
UNION ALL
SELECT 'orders with no payment', COUNT(*)
FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_payments p WHERE p.order_id = o.order_id)
UNION ALL
SELECT 'orders with no review', COUNT(*)
FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_reviews r WHERE r.order_id = o.order_id)
UNION ALL
SELECT 'customer zips missing from geolocation', COUNT(DISTINCT c.customer_zip_code_prefix)
FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM geolocation g WHERE g.geolocation_zip_code_prefix = c.customer_zip_code_prefix)
UNION ALL
SELECT 'seller zips missing from geolocation', COUNT(DISTINCT s.seller_zip_code_prefix)
FROM sellers s
WHERE NOT EXISTS (SELECT 1 FROM geolocation g WHERE g.geolocation_zip_code_prefix = s.seller_zip_code_prefix);

-- @name dq_product_gaps
SELECT
    SUM(p.product_category_name IS NULL)                                  AS products_no_category,
    SUM(p.product_category_name IS NOT NULL AND t.product_category_name IS NULL) AS products_category_untranslated,
    SUM(p.product_weight_g IS NULL OR p.product_weight_g = 0)             AS products_missing_weight,
    SUM(p.product_length_cm IS NULL OR p.product_height_cm IS NULL OR p.product_width_cm IS NULL) AS products_missing_dims
FROM products p
LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name;

-- @name dq_untranslated_categories
SELECT p.product_category_name, COUNT(*) AS n_products
FROM products p
LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
WHERE p.product_category_name IS NOT NULL AND t.product_category_name IS NULL
GROUP BY p.product_category_name;

-- @name dq_payment_vs_items
-- Payment total vs items total. Small gaps are rounding/vouchers; large
-- gaps flag orders to treat with care in revenue figures.
WITH pay AS (
    SELECT order_id, SUM(payment_value) AS paid FROM order_payments GROUP BY order_id
)
SELECT
    COUNT(*)                                                    AS orders_compared,
    SUM(ABS(pay.paid - v.gross_order_value) <= 1.0)             AS within_1_brl,
    SUM(ABS(pay.paid - v.gross_order_value) >  1.0)             AS off_by_more_than_1_brl,
    ROUND(SUM(pay.paid), 2)                                     AS total_paid,
    ROUND(SUM(v.gross_order_value), 2)                          AS total_items_plus_freight
FROM pay
JOIN v_order_value v ON v.order_id = pay.order_id;

-- @name dq_numeric_ranges
SELECT
    MIN(price)          AS min_price,     MAX(price)          AS max_price,
    MIN(freight_value)  AS min_freight,   MAX(freight_value)  AS max_freight,
    SUM(price <= 0)     AS nonpositive_prices,
    SUM(freight_value < 0) AS negative_freight
FROM order_items;
