-- =====================================================================
-- 05_seller_category_perf.sql  —  Q4 top categories and sellers
--
-- Item-level revenue (price) is attributed to the item's category/seller.
-- Order-level outcomes (late, review score) are attributed to every
-- category/seller on the order.
-- Late = delivered on a later calendar date than the estimate shown at
-- checkout (same definition as 06 and the model target).
-- =====================================================================

-- @name q4_category_revenue_pareto
WITH items AS (
    SELECT
        COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
        i.order_id, i.price
    FROM order_items i
    JOIN orders o        ON o.order_id = i.order_id
    LEFT JOIN products p ON p.product_id = i.product_id
    LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
),
cat AS (
    SELECT category,
           COUNT(DISTINCT order_id) AS n_orders,
           COUNT(*)                 AS n_items,
           SUM(price)               AS revenue,
           AVG(price)               AS avg_item_price
    FROM items
    GROUP BY category
)
SELECT
    DENSE_RANK() OVER (ORDER BY revenue DESC)                              AS revenue_rank,
    category,
    n_orders,
    n_items,
    ROUND(revenue, 2)                                                      AS revenue,
    ROUND(avg_item_price, 2)                                               AS avg_item_price,
    ROUND(100.0 * revenue / SUM(revenue) OVER (), 2)                       AS pct_of_revenue,
    ROUND(100.0 * SUM(revenue) OVER (ORDER BY revenue DESC ROWS UNBOUNDED PRECEDING)
                / SUM(revenue) OVER (), 1)                                 AS cumulative_pct
FROM cat
ORDER BY revenue_rank;

-- @name q4_category_quality
-- Revenue alongside review score and late rate, for categories with at
-- least 200 delivered orders.
WITH order_cat AS (
    SELECT DISTINCT
        i.order_id,
        COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category
    FROM order_items i
    LEFT JOIN products p ON p.product_id = i.product_id
    LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
),
delivered AS (
    SELECT order_id,
           CASE WHEN date(order_delivered_customer_date) > date(order_estimated_delivery_date)
                THEN 1 ELSE 0 END AS is_late,
           julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp) AS delivery_days
    FROM orders
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
      AND order_estimated_delivery_date IS NOT NULL
)
SELECT
    oc.category,
    COUNT(*)                                  AS delivered_orders,
    ROUND(AVG(r.review_score), 2)             AS avg_review,
    ROUND(100.0 * AVG(d.is_late), 2)          AS late_rate_pct,
    ROUND(AVG(d.delivery_days), 1)            AS avg_delivery_days,
    RANK() OVER (ORDER BY AVG(r.review_score) ASC)  AS worst_review_rank
FROM order_cat oc
JOIN delivered d          ON d.order_id = oc.order_id
LEFT JOIN v_order_reviews r ON r.order_id = oc.order_id
GROUP BY oc.category
HAVING COUNT(*) >= 200
ORDER BY late_rate_pct DESC;

-- @name q4_category_growth_h1
-- H1 2018 vs H1 2017 revenue by category: who is driving growth?
WITH items AS (
    SELECT
        COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
        CASE
            WHEN date(o.order_purchase_timestamp) BETWEEN '2017-01-01' AND '2017-06-30' THEN 'h1_2017'
            WHEN date(o.order_purchase_timestamp) BETWEEN '2018-01-01' AND '2018-06-30' THEN 'h1_2018'
        END AS period,
        i.price
    FROM order_items i
    JOIN orders o ON o.order_id = i.order_id
    LEFT JOIN products p ON p.product_id = i.product_id
    LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
),
pivoted AS (
    SELECT category,
           SUM(CASE WHEN period = 'h1_2017' THEN price ELSE 0 END) AS rev_h1_2017,
           SUM(CASE WHEN period = 'h1_2018' THEN price ELSE 0 END) AS rev_h1_2018
    FROM items
    WHERE period IS NOT NULL
    GROUP BY category
)
SELECT
    category,
    ROUND(rev_h1_2017, 2)                                                AS rev_h1_2017,
    ROUND(rev_h1_2018, 2)                                                AS rev_h1_2018,
    ROUND(rev_h1_2018 - rev_h1_2017, 2)                                  AS abs_change,
    ROUND(100.0 * (rev_h1_2018 - rev_h1_2017) / NULLIF(rev_h1_2017, 0), 1) AS growth_pct,
    ROUND(100.0 * (rev_h1_2018 - rev_h1_2017)
                / SUM(rev_h1_2018 - rev_h1_2017) OVER (), 1)             AS share_of_total_growth_pct
FROM pivoted
ORDER BY abs_change DESC;

-- @name q4_top3_categories_per_state
WITH st_cat AS (
    SELECT
        c.customer_state AS state,
        COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
        SUM(i.price) AS revenue
    FROM order_items i
    JOIN orders o    ON o.order_id = i.order_id
    JOIN customers c ON c.customer_id = o.customer_id
    LEFT JOIN products p ON p.product_id = i.product_id
    LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
    GROUP BY state, category
),
ranked AS (
    SELECT state, category, revenue,
           ROW_NUMBER() OVER (PARTITION BY state ORDER BY revenue DESC) AS rn,
           100.0 * revenue / SUM(revenue) OVER (PARTITION BY state)     AS pct_of_state
    FROM st_cat
)
SELECT state, rn AS rank_in_state, category, ROUND(revenue, 2) AS revenue,
       ROUND(pct_of_state, 1) AS pct_of_state_revenue
FROM ranked
WHERE rn <= 3
ORDER BY state, rn;

-- @name q4_top_sellers
-- Top 25 sellers by revenue (min. 30 orders) with service quality.
WITH seller_orders AS (
    SELECT i.seller_id, i.order_id, SUM(i.price) AS revenue
    FROM order_items i
    JOIN orders o ON o.order_id = i.order_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
    GROUP BY i.seller_id, i.order_id
),
outcomes AS (
    SELECT order_id,
           CASE WHEN date(order_delivered_customer_date) > date(order_estimated_delivery_date)
                THEN 1.0 ELSE 0.0 END AS is_late
    FROM orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
),
agg AS (
    SELECT
        so.seller_id,
        COUNT(*)                        AS n_orders,
        SUM(so.revenue)                 AS revenue,
        AVG(r.review_score)             AS avg_review,
        AVG(oc.is_late)                 AS late_rate
    FROM seller_orders so
    LEFT JOIN outcomes oc       ON oc.order_id = so.order_id
    LEFT JOIN v_order_reviews r ON r.order_id  = so.order_id
    GROUP BY so.seller_id
    HAVING COUNT(*) >= 30
)
SELECT
    RANK() OVER (ORDER BY a.revenue DESC)         AS revenue_rank,
    a.seller_id,
    s.seller_state,
    a.n_orders,
    ROUND(a.revenue, 2)                           AS revenue,
    ROUND(a.revenue / a.n_orders, 2)              AS revenue_per_order,
    ROUND(a.avg_review, 2)                        AS avg_review,
    ROUND(100.0 * a.late_rate, 1)                 AS late_rate_pct,
    NTILE(4) OVER (ORDER BY a.avg_review DESC)    AS review_quartile   -- 1 = best
FROM agg a
JOIN sellers s ON s.seller_id = a.seller_id
ORDER BY revenue_rank
LIMIT 25;

-- @name q4_seller_concentration
-- What share of revenue comes from the top 1% / 10% / 20% of sellers?
WITH seller_rev AS (
    SELECT i.seller_id, SUM(i.price) AS revenue
    FROM order_items i
    JOIN orders o ON o.order_id = i.order_id
    WHERE o.order_status NOT IN ('canceled', 'unavailable')
    GROUP BY i.seller_id
),
ranked AS (
    SELECT revenue,
           PERCENT_RANK() OVER (ORDER BY revenue DESC) AS pr,
           SUM(revenue) OVER ()                        AS total
    FROM seller_rev
)
SELECT
    (SELECT COUNT(*) FROM seller_rev)                                      AS n_sellers,
    ROUND(100.0 * SUM(CASE WHEN pr < 0.01 THEN revenue END) / MAX(total), 1) AS top1pct_share,
    ROUND(100.0 * SUM(CASE WHEN pr < 0.10 THEN revenue END) / MAX(total), 1) AS top10pct_share,
    ROUND(100.0 * SUM(CASE WHEN pr < 0.20 THEN revenue END) / MAX(total), 1) AS top20pct_share
FROM ranked;
