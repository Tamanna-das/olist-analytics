-- =====================================================================
-- 04_cohort_retention.sql  —  Q3 repeat purchase & cohort retention
--
-- The person is customer_unique_id (customer_id changes on every order).
-- Valid order = order_status NOT IN ('canceled','unavailable').
-- Cohort      = month of a person's first valid purchase.
-- Caveat: later cohorts have less time to repeat (right-censoring), so
-- compare cohorts at the same month offset, not on lifetime totals.
-- =====================================================================

-- @name q3_repeat_rate_overall
WITH person_orders AS (
    SELECT c.customer_unique_id, COUNT(DISTINCT o.order_id) AS n_orders
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
    GROUP BY c.customer_unique_id
)
SELECT
    COUNT(*)                                             AS n_customers,
    SUM(n_orders >= 2)                                   AS n_repeat_customers,
    ROUND(100.0 * SUM(n_orders >= 2) / COUNT(*), 2)      AS repeat_rate_pct,
    ROUND(AVG(n_orders), 3)                              AS orders_per_customer,
    ROUND(100.0 * SUM(CASE WHEN n_orders >= 2 THEN n_orders END)
                / SUM(n_orders), 2)                      AS pct_orders_from_repeaters
FROM person_orders;

-- @name q3_cohort_retention_matrix
-- Long format: one row per (cohort, month_offset). Pivot in pandas or
-- Power BI (matrix visual) for the triangle.
WITH person_orders AS (
    SELECT
        c.customer_unique_id,
        o.order_id,
        o.order_purchase_timestamp,
        CAST(strftime('%Y', o.order_purchase_timestamp) AS INT) * 12
          + CAST(strftime('%m', o.order_purchase_timestamp) AS INT) AS month_idx
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
),
first_purchase AS (
    SELECT customer_unique_id, MIN(month_idx) AS cohort_idx
    FROM person_orders
    GROUP BY customer_unique_id
),
activity AS (
    SELECT DISTINCT
        f.cohort_idx,
        po.month_idx - f.cohort_idx AS month_offset,
        po.customer_unique_id
    FROM person_orders po
    JOIN first_purchase f USING (customer_unique_id)
),
counts AS (
    SELECT cohort_idx, month_offset, COUNT(*) AS active_customers
    FROM activity
    GROUP BY cohort_idx, month_offset
)
SELECT
    printf('%04d-%02d', (cohort_idx - 1) / 12, (cohort_idx - 1) % 12 + 1) AS cohort_month,
    month_offset,
    active_customers,
    FIRST_VALUE(active_customers) OVER (PARTITION BY cohort_idx ORDER BY month_offset) AS cohort_size,
    ROUND(100.0 * active_customers
          / FIRST_VALUE(active_customers) OVER (PARTITION BY cohort_idx ORDER BY month_offset), 2) AS retention_pct
FROM counts
WHERE cohort_idx BETWEEN 2017 * 12 + 1 AND 2018 * 12 + 8   -- cohorts 2017-01 .. 2018-08
  AND month_offset <= 12
ORDER BY cohort_idx, month_offset;

-- @name q3_repeat_within_90d_by_cohort
-- Fairer cohort comparison: share of each cohort that buys again within
-- 90 days of the first order. Cohorts whose 90-day window runs past the
-- end of the data are flagged.
WITH person_orders AS (
    SELECT c.customer_unique_id, o.order_purchase_timestamp AS ts
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
),
seq AS (
    SELECT
        customer_unique_id,
        ts,
        ROW_NUMBER() OVER (PARTITION BY customer_unique_id ORDER BY ts) AS order_seq,
        LEAD(ts)     OVER (PARTITION BY customer_unique_id ORDER BY ts) AS next_ts
    FROM person_orders
),
firsts AS (
    SELECT
        customer_unique_id,
        strftime('%Y-%m', ts)                          AS cohort_month,
        julianday(next_ts) - julianday(ts)             AS days_to_second
    FROM seq
    WHERE order_seq = 1
),
data_end AS (SELECT MAX(ts) AS max_ts FROM person_orders)
SELECT
    f.cohort_month,
    COUNT(*)                                                         AS cohort_size,
    SUM(f.days_to_second <= 90)                                      AS repeat_90d,
    ROUND(100.0 * SUM(f.days_to_second <= 90) / COUNT(*), 2)         AS repeat_90d_pct,
    CASE WHEN date(f.cohort_month || '-01', '+1 month', '+90 days') > date((SELECT max_ts FROM data_end))
         THEN 'incomplete window' ELSE 'complete' END                AS window_status
FROM firsts f
WHERE f.cohort_month BETWEEN '2017-01' AND '2018-08'
GROUP BY f.cohort_month
ORDER BY f.cohort_month;

-- @name q3_time_to_second_purchase
-- Distribution of days between first and second purchase (repeaters only).
-- Same-day second orders are common in Olist (split baskets) — reported
-- separately so they don't flatter the repeat rate.
WITH person_orders AS (
    SELECT c.customer_unique_id, o.order_purchase_timestamp AS ts
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
),
seq AS (
    SELECT customer_unique_id, ts,
           ROW_NUMBER() OVER (PARTITION BY customer_unique_id ORDER BY ts) AS order_seq,
           LEAD(ts)     OVER (PARTITION BY customer_unique_id ORDER BY ts) AS next_ts
    FROM person_orders
),
gaps AS (
    SELECT julianday(next_ts) - julianday(ts) AS gap_days
    FROM seq
    WHERE order_seq = 1 AND next_ts IS NOT NULL
)
SELECT
    CASE
        WHEN gap_days < 1   THEN '0: same day'
        WHEN gap_days <= 30 THEN '1: 1-30 days'
        WHEN gap_days <= 90 THEN '2: 31-90 days'
        WHEN gap_days <= 180 THEN '3: 91-180 days'
        ELSE                     '4: >180 days'
    END                                                    AS gap_bucket,
    COUNT(*)                                               AS n_customers,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)     AS pct
FROM gaps
GROUP BY gap_bucket
ORDER BY gap_bucket;

-- @name q3_repeat_rate_excl_same_day
-- 31% of "second purchases" happen the same day as the first (split
-- baskets). This counts a repeat only when a later order is placed on a
-- different calendar day.
WITH person_days AS (
    SELECT c.customer_unique_id,
           COUNT(DISTINCT date(o.order_purchase_timestamp)) AS n_purchase_days
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
    GROUP BY c.customer_unique_id
)
SELECT
    COUNT(*)                                               AS n_customers,
    SUM(n_purchase_days >= 2)                              AS n_repeat_customers,
    ROUND(100.0 * SUM(n_purchase_days >= 2) / COUNT(*), 2) AS repeat_rate_pct
FROM person_days;
