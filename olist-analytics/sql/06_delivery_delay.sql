-- =====================================================================
-- 06_delivery_delay.sql  —  Q5 delivery vs estimate, Q6 reviews vs delay
--
-- Scope: delivered orders with both delivery and estimate dates.
-- delay_days = date(delivered) - date(estimated)   (>0 means late)
-- is_late    = delay_days > 0   (identical to the model target)
-- =====================================================================

-- @name q5_delivery_summary
WITH d AS (
    SELECT
        julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp)      AS actual_days,
        julianday(order_estimated_delivery_date) - julianday(order_purchase_timestamp)      AS promised_days,
        julianday(date(order_delivered_customer_date)) - julianday(date(order_estimated_delivery_date)) AS delay_days
    FROM orders
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
      AND order_estimated_delivery_date IS NOT NULL
)
SELECT
    COUNT(*)                                   AS delivered_orders,
    ROUND(AVG(actual_days), 1)                 AS avg_actual_days,
    ROUND(AVG(promised_days), 1)               AS avg_promised_days,
    ROUND(AVG(delay_days), 1)                  AS avg_delay_days,     -- negative = early on average
    SUM(delay_days > 0)                        AS late_orders,
    ROUND(100.0 * AVG(delay_days > 0), 2)      AS late_rate_pct,
    ROUND(AVG(CASE WHEN delay_days > 0 THEN delay_days END), 1) AS avg_days_late_when_late
FROM d;

-- @name q5_late_rate_monthly
-- Late rate by purchase month with a 3-month rolling average and the
-- change vs the previous month.
WITH d AS (
    SELECT
        strftime('%Y-%m', order_purchase_timestamp) AS ym,
        CASE WHEN date(order_delivered_customer_date) > date(order_estimated_delivery_date) THEN 1.0 ELSE 0.0 END AS is_late,
        julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp) AS actual_days,
        julianday(order_estimated_delivery_date) - julianday(order_purchase_timestamp) AS promised_days
    FROM orders
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
      AND order_estimated_delivery_date IS NOT NULL
      AND date(order_purchase_timestamp) BETWEEN '2017-01-01' AND '2018-08-31'
),
m AS (
    SELECT ym, COUNT(*) AS n, AVG(is_late) AS late_rate,
           AVG(actual_days) AS actual_days, AVG(promised_days) AS promised_days
    FROM d GROUP BY ym
)
SELECT
    ym,
    n                                                                        AS delivered_orders,
    ROUND(100.0 * late_rate, 2)                                              AS late_rate_pct,
    ROUND(100.0 * AVG(late_rate) OVER (ORDER BY ym ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS late_rate_3m_avg,
    ROUND(100.0 * (late_rate - LAG(late_rate) OVER (ORDER BY ym)), 2)        AS change_pp,
    ROUND(actual_days, 1)                                                    AS avg_actual_days,
    ROUND(promised_days, 1)                                                  AS avg_promised_days
FROM m
ORDER BY ym;

-- @name q5_late_rate_by_customer_state
WITH d AS (
    SELECT
        c.customer_state AS state,
        CASE WHEN date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date) THEN 1.0 ELSE 0.0 END AS is_late,
        julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp) AS actual_days
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
      AND o.order_estimated_delivery_date IS NOT NULL
)
SELECT
    state,
    COUNT(*)                                         AS delivered_orders,
    ROUND(100.0 * AVG(is_late), 2)                   AS late_rate_pct,
    ROUND(AVG(actual_days), 1)                       AS avg_delivery_days,
    RANK() OVER (ORDER BY AVG(is_late) DESC)         AS late_rank
FROM d
GROUP BY state
HAVING COUNT(*) >= 100
ORDER BY late_rate_pct DESC;

-- @name q5_late_rate_by_route
-- Seller state -> customer state lanes with >= 300 orders.
-- Uses the order's first item's seller (most orders have one seller).
WITH first_seller AS (
    SELECT order_id, seller_id
    FROM (SELECT order_id, seller_id,
                 ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY order_item_id) AS rn
          FROM order_items)
    WHERE rn = 1
),
d AS (
    SELECT
        s.seller_state || ' -> ' || c.customer_state AS route,
        (s.seller_state = c.customer_state)          AS same_state,
        CASE WHEN date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date) THEN 1.0 ELSE 0.0 END AS is_late,
        julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp) AS actual_days
    FROM orders o
    JOIN customers c     ON c.customer_id = o.customer_id
    JOIN first_seller fs ON fs.order_id = o.order_id
    JOIN sellers s       ON s.seller_id = fs.seller_id
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
      AND o.order_estimated_delivery_date IS NOT NULL
)
SELECT
    route,
    MAX(same_state)                       AS same_state,
    COUNT(*)                              AS delivered_orders,
    ROUND(100.0 * AVG(is_late), 2)        AS late_rate_pct,
    ROUND(AVG(actual_days), 1)            AS avg_delivery_days
FROM d
GROUP BY route
HAVING COUNT(*) >= 300
ORDER BY late_rate_pct DESC;

-- @name q5_delay_stage_breakdown
-- Where does the time go? approval -> seller handover -> carrier transit,
-- split by on-time vs late orders. Also: did the seller miss the
-- shipping_limit_date?
WITH limits AS (
    SELECT order_id, MAX(shipping_limit_date) AS shipping_limit
    FROM order_items GROUP BY order_id
),
d AS (
    SELECT
        CASE WHEN date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date)
             THEN 'late' ELSE 'on_time' END                                                     AS outcome,
        julianday(o.order_approved_at)             - julianday(o.order_purchase_timestamp)      AS approval_days,
        julianday(o.order_delivered_carrier_date)  - julianday(o.order_approved_at)             AS seller_handover_days,
        julianday(o.order_delivered_customer_date) - julianday(o.order_delivered_carrier_date)  AS carrier_transit_days,
        CASE WHEN o.order_delivered_carrier_date > l.shipping_limit THEN 1.0 ELSE 0.0 END        AS missed_ship_limit
    FROM orders o
    JOIN limits l ON l.order_id = o.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
      AND o.order_delivered_carrier_date  IS NOT NULL
      AND o.order_approved_at IS NOT NULL
      AND o.order_estimated_delivery_date IS NOT NULL
)
SELECT
    outcome,
    COUNT(*)                                   AS n_orders,
    ROUND(AVG(approval_days), 2)               AS avg_approval_days,
    ROUND(AVG(seller_handover_days), 2)        AS avg_seller_handover_days,
    ROUND(AVG(carrier_transit_days), 2)        AS avg_carrier_transit_days,
    ROUND(100.0 * AVG(missed_ship_limit), 1)   AS pct_missed_shipping_limit
FROM d
GROUP BY outcome;

-- @name q6_review_vs_delay
-- Q6: review score by delay bucket. The key business link: lateness
-- drives 1-star reviews.
WITH d AS (
    SELECT
        o.order_id,
        julianday(date(o.order_delivered_customer_date)) - julianday(date(o.order_estimated_delivery_date)) AS delay_days
    FROM orders o
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
      AND o.order_estimated_delivery_date IS NOT NULL
),
b AS (
    SELECT
        d.order_id,
        CASE
            WHEN delay_days <= -14 THEN '1: early 14+ days'
            WHEN delay_days <= -7  THEN '2: early 7-13 days'
            WHEN delay_days <= 0   THEN '3: early 0-6 days'
            WHEN delay_days <= 3   THEN '4: late 1-3 days'
            WHEN delay_days <= 7   THEN '5: late 4-7 days'
            WHEN delay_days <= 14  THEN '6: late 8-14 days'
            ELSE                        '7: late 15+ days'
        END AS delay_bucket,
        r.review_score
    FROM d
    JOIN v_order_reviews r ON r.order_id = d.order_id
)
SELECT
    delay_bucket,
    COUNT(*)                                               AS n_reviews,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS pct_of_reviews,
    ROUND(AVG(review_score), 2)                            AS avg_review,
    ROUND(100.0 * AVG(review_score = 1), 1)                AS pct_1_star,
    ROUND(100.0 * AVG(review_score = 5), 1)                AS pct_5_star
FROM b
GROUP BY delay_bucket
ORDER BY delay_bucket;

-- @name q6_review_late_vs_ontime
-- Headline comparison for the README.
WITH d AS (
    SELECT
        o.order_id,
        CASE WHEN date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date)
             THEN 'late' ELSE 'on_time' END AS outcome
    FROM orders o
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
      AND o.order_estimated_delivery_date IS NOT NULL
)
SELECT
    d.outcome,
    COUNT(*)                                     AS n_reviews,
    ROUND(AVG(r.review_score), 2)                AS avg_review,
    ROUND(100.0 * AVG(r.review_score <= 2), 1)   AS pct_1_or_2_star
FROM d
JOIN v_order_reviews r ON r.order_id = d.order_id
GROUP BY d.outcome;

-- @name q5_late_orders_by_route_share
-- Where do the late orders actually come from? Share of ALL late orders
-- per lane, with cumulative share: the routes to fix first.
WITH first_seller AS (
    SELECT order_id, seller_id
    FROM (SELECT order_id, seller_id,
                 ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY order_item_id) AS rn
          FROM order_items)
    WHERE rn = 1
),
d AS (
    SELECT
        s.seller_state || ' -> ' || c.customer_state AS route,
        CASE WHEN date(o.order_delivered_customer_date) > date(o.order_estimated_delivery_date) THEN 1 ELSE 0 END AS is_late
    FROM orders o
    JOIN customers c     ON c.customer_id = o.customer_id
    JOIN first_seller fs ON fs.order_id = o.order_id
    JOIN sellers s       ON s.seller_id = fs.seller_id
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
      AND o.order_estimated_delivery_date IS NOT NULL
),
r AS (
    SELECT route, COUNT(*) AS delivered_orders, SUM(is_late) AS late_orders
    FROM d GROUP BY route
)
SELECT
    route,
    delivered_orders,
    late_orders,
    ROUND(100.0 * late_orders / delivered_orders, 2)                    AS late_rate_pct,
    ROUND(100.0 * late_orders / SUM(late_orders) OVER (), 1)            AS pct_of_all_late,
    ROUND(100.0 * SUM(late_orders) OVER (ORDER BY late_orders DESC, route
                 ROWS UNBOUNDED PRECEDING) / SUM(late_orders) OVER (), 1) AS cumulative_pct_of_late
FROM r
ORDER BY late_orders DESC
LIMIT 20;
