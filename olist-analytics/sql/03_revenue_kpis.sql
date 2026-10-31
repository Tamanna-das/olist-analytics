-- =====================================================================
-- 03_revenue_kpis.sql  —  Q1 monthly revenue & growth, Q2 order value
--
-- Definitions (state these in the README / dashboard):
--   * Revenue      = SUM(price) over order_items  (product revenue, BRL,
--                    excludes freight). Freight reported separately.
--   * Valid order  = order_status NOT IN ('canceled','unavailable').
--   * Trend window = purchase months 2017-01 .. 2018-08. Earlier and later
--                    months are too sparse (see dq_purchase_date_coverage).
-- =====================================================================

-- @name q1_monthly_revenue_growth
-- Monthly revenue, orders, MoM and YoY growth, 3-month rolling average,
-- running total.
WITH valid_orders AS (
    SELECT order_id, strftime('%Y-%m', order_purchase_timestamp) AS ym
    FROM orders
    WHERE order_status NOT IN ('canceled', 'unavailable')
      AND date(order_purchase_timestamp) BETWEEN '2017-01-01' AND '2018-08-31'
),
monthly AS (
    SELECT
        vo.ym,
        COUNT(DISTINCT vo.order_id)      AS n_orders,
        ROUND(SUM(v.product_revenue), 2) AS revenue,
        ROUND(SUM(v.freight_revenue), 2) AS freight
    FROM valid_orders vo
    JOIN v_order_value v ON v.order_id = vo.order_id
    GROUP BY vo.ym
)
SELECT
    ym,
    n_orders,
    revenue,
    freight,
    LAG(revenue) OVER (ORDER BY ym)                                         AS prev_month_revenue,
    ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY ym))
                / LAG(revenue) OVER (ORDER BY ym), 1)                      AS mom_growth_pct,
    ROUND(100.0 * (revenue - LAG(revenue, 12) OVER (ORDER BY ym))
                / LAG(revenue, 12) OVER (ORDER BY ym), 1)                  AS yoy_growth_pct,
    ROUND(AVG(revenue) OVER (ORDER BY ym ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS revenue_3m_avg,
    ROUND(SUM(revenue) OVER (ORDER BY ym ROWS UNBOUNDED PRECEDING), 2)     AS revenue_running_total
FROM monthly
ORDER BY ym;

-- @name q1_best_and_worst_months
-- Rank months by revenue and by MoM growth.
WITH monthly AS (
    SELECT strftime('%Y-%m', o.order_purchase_timestamp) AS ym,
           SUM(v.product_revenue) AS revenue
    FROM orders o
    JOIN v_order_value v ON v.order_id = o.order_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
      AND date(o.order_purchase_timestamp) BETWEEN '2017-01-01' AND '2018-08-31'
    GROUP BY ym
),
growth AS (
    SELECT ym, revenue,
           100.0 * (revenue - LAG(revenue) OVER (ORDER BY ym)) / LAG(revenue) OVER (ORDER BY ym) AS mom_pct
    FROM monthly
)
SELECT
    ym,
    ROUND(revenue, 2)                                AS revenue,
    ROUND(mom_pct, 1)                                AS mom_growth_pct,
    RANK() OVER (ORDER BY revenue DESC)              AS revenue_rank,
    RANK() OVER (ORDER BY mom_pct DESC)              AS growth_rank
FROM growth
ORDER BY revenue_rank;

-- @name q2_aov_overall
-- Average order value (product revenue per valid order), plus median and
-- percentiles computed with window functions (SQLite has no MEDIAN()).
WITH ov AS (
    SELECT v.product_revenue AS value
    FROM orders o
    JOIN v_order_value v ON v.order_id = o.order_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
),
ranked AS (
    SELECT value,
           ROW_NUMBER() OVER (ORDER BY value) AS rn,
           COUNT(*)     OVER ()               AS n
    FROM ov
)
SELECT
    MAX(n)                                                             AS n_orders,
    ROUND(AVG(value), 2)                                               AS aov_mean,
    ROUND(MAX(CASE WHEN rn = CAST(0.25 * n AS INT) + 1 THEN value END), 2) AS p25,
    ROUND(MAX(CASE WHEN rn = CAST(0.50 * n AS INT) + 1 THEN value END), 2) AS median,
    ROUND(MAX(CASE WHEN rn = CAST(0.75 * n AS INT) + 1 THEN value END), 2) AS p75,
    ROUND(MAX(CASE WHEN rn = CAST(0.95 * n AS INT) + 1 THEN value END), 2) AS p95
FROM ranked;

-- @name q2_aov_monthly
-- Monthly AOV and items per order; is growth coming from more orders or
-- bigger baskets?
WITH ov AS (
    SELECT strftime('%Y-%m', o.order_purchase_timestamp) AS ym,
           v.product_revenue, v.n_items
    FROM orders o
    JOIN v_order_value v ON v.order_id = o.order_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
      AND date(o.order_purchase_timestamp) BETWEEN '2017-01-01' AND '2018-08-31'
)
SELECT
    ym,
    COUNT(*)                         AS n_orders,
    ROUND(AVG(product_revenue), 2)   AS aov,
    ROUND(AVG(n_items), 3)           AS items_per_order,
    ROUND(AVG(product_revenue) - LAG(AVG(product_revenue)) OVER (ORDER BY ym), 2) AS aov_change_vs_prev
FROM ov
GROUP BY ym
ORDER BY ym;

-- @name q2_order_value_deciles
-- How concentrated is revenue across order sizes?
WITH ov AS (
    SELECT v.product_revenue AS value
    FROM orders o
    JOIN v_order_value v ON v.order_id = o.order_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
),
bucketed AS (
    SELECT value, NTILE(10) OVER (ORDER BY value) AS decile FROM ov
)
SELECT
    decile,
    COUNT(*)                                                    AS n_orders,
    ROUND(MIN(value), 2)                                        AS min_value,
    ROUND(MAX(value), 2)                                        AS max_value,
    ROUND(SUM(value), 2)                                        AS revenue,
    ROUND(100.0 * SUM(value) / SUM(SUM(value)) OVER (), 1)      AS pct_of_revenue
FROM bucketed
GROUP BY decile
ORDER BY decile;

-- @name q2_revenue_by_customer_state
WITH st AS (
    SELECT c.customer_state AS state,
           COUNT(DISTINCT o.order_id) AS n_orders,
           SUM(v.product_revenue)     AS revenue,
           AVG(v.freight_revenue)     AS avg_freight
    FROM orders o
    JOIN customers c     ON c.customer_id = o.customer_id
    JOIN v_order_value v ON v.order_id = o.order_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
    GROUP BY c.customer_state
)
SELECT
    state,
    n_orders,
    ROUND(revenue, 2)                                                      AS revenue,
    ROUND(revenue / n_orders, 2)                                           AS aov,
    ROUND(avg_freight, 2)                                                  AS avg_freight,
    ROUND(100.0 * revenue / SUM(revenue) OVER (), 2)                       AS pct_of_revenue,
    ROUND(100.0 * SUM(revenue) OVER (ORDER BY revenue DESC
                 ROWS UNBOUNDED PRECEDING) / SUM(revenue) OVER (), 1)      AS cumulative_pct
FROM st
ORDER BY revenue DESC;
