# Power BI dashboard — spec

**Audience:** Olist **Operations Manager (seller logistics)**. They decide which sellers, lanes and categories need intervention to cut late deliveries, and they report monthly growth to leadership. Every visual should answer a question this person asks; anything else is cut.

**Questions the dashboard answers**
1. Are we growing, and is growth coming from more orders or bigger baskets? (Overview)
2. Where and when are deliveries late, and what does lateness cost us in reviews? (Delivery)
3. Which sellers and categories drive revenue, and which of them break the delivery promise? (Sellers & categories)

---

## 1. Data

Run `python src/export_powerbi.py` and import the four CSVs from `dashboard/data/` (Get Data → Text/CSV).

| Table | Grain | Key columns |
|---|---|---|
| `fact_orders` | one valid order | order_id, customer_unique_id, purchase_date, product_revenue, freight_value, main_seller_id, main_category, is_delivered, is_late, delay_days, delivery_days, promised_days, review_score |
| `fact_order_items` | one item | order_id, seller_id, category, price, freight_value |
| `dim_customer` | one person (customer_unique_id) | customer_state, first_purchase_date, cohort_month, n_orders |
| `dim_seller` | one seller | seller_id, seller_state |
| `dim_date` | one day | built in DAX below |

**Power Query types:** set `purchase_date` and `first_purchase_date` to *Date*; `is_late`, `is_delivered`, `review_score` to *Whole number*; money columns to *Fixed decimal*.

### Date table (Modeling → New table)
```DAX
dim_date =
ADDCOLUMNS (
    CALENDAR ( DATE ( 2016, 9, 1 ), DATE ( 2018, 10, 31 ) ),
    "Year", YEAR ( [Date] ),
    "Month", FORMAT ( [Date], "YYYY-MM" ),
    "Month Start", DATE ( YEAR ( [Date] ), MONTH ( [Date] ), 1 ),
    "Weekday", FORMAT ( [Date], "ddd" ),
    "In Trend Window", [Date] >= DATE ( 2017, 1, 1 ) && [Date] <= DATE ( 2018, 8, 31 )
)
```
Mark it as a date table (Table tools → Mark as date table → `Date`).

### Relationships (Model view)
| From (1) | To (*) | Direction |
|---|---|---|
| `dim_date[Date]` | `fact_orders[purchase_date]` | single |
| `dim_customer[customer_unique_id]` | `fact_orders[customer_unique_id]` | single |
| `fact_orders[order_id]` | `fact_order_items[order_id]` | single (orders → items) |
| `dim_seller[seller_id]` | `fact_order_items[seller_id]` | single |

Date and customer filters reach items through `fact_orders`. Seller and category filters live on items; the delivery measures below use `CROSSFILTER` to push them back to orders, so no bidirectional relationship is needed.

### Calculated column (fact_orders)
```DAX
Delay Bucket =
SWITCH (
    TRUE (),
    ISBLANK ( fact_orders[delay_days] ), BLANK (),
    fact_orders[delay_days] <= -7, "1 Early 7+ days",
    fact_orders[delay_days] <= 0,  "2 On time / early <7",
    fact_orders[delay_days] <= 3,  "3 Late 1-3 days",
    fact_orders[delay_days] <= 7,  "4 Late 4-7 days",
    "5 Late 8+ days"
)
```

---

## 2. DAX measures
Create a blank table `_Measures` and put these in it.

```DAX
-- Revenue and orders ------------------------------------------------------
Revenue = SUM ( fact_order_items[price] )
Freight = SUM ( fact_order_items[freight_value] )
Orders = DISTINCTCOUNT ( fact_orders[order_id] )
AOV = DIVIDE ( [Revenue], [Orders] )
Items per Order = DIVIDE ( COUNTROWS ( fact_order_items ), [Orders] )

Revenue PM = CALCULATE ( [Revenue], DATEADD ( dim_date[Date], -1, MONTH ) )
Revenue MoM % = DIVIDE ( [Revenue] - [Revenue PM], [Revenue PM] )
Revenue PY = CALCULATE ( [Revenue], SAMEPERIODLASTYEAR ( dim_date[Date] ) )
Revenue YoY % = DIVIDE ( [Revenue] - [Revenue PY], [Revenue PY] )
Revenue 3M Avg =
VAR LastDay = MAX ( dim_date[Date] )
VAR Window = DATESINPERIOD ( dim_date[Date], LastDay, -3, MONTH )
RETURN
    DIVIDE (
        CALCULATE ( [Revenue], Window ),
        CALCULATE ( DISTINCTCOUNT ( dim_date[Month] ), Window )
    )

-- Customers -----------------------------------------------------------------
Customers = DISTINCTCOUNT ( fact_orders[customer_unique_id] )
Repeat Customers =
COUNTROWS (
    FILTER (
        VALUES ( fact_orders[customer_unique_id] ),
        CALCULATE ( [Orders], ALLEXCEPT ( fact_orders, fact_orders[customer_unique_id] ) ) >= 2
    )
)
Repeat Rate % = DIVIDE ( [Repeat Customers], [Customers] )
-- "Customers active in the selected period who have 2+ orders in total".
-- ALLEXCEPT removes the date filter on purpose; use the SQL cohort table
-- (q3_cohort_retention_matrix) for period-correct retention.

-- Delivery (CROSSFILTER lets seller/category slicers on items reach orders) --
Delivered Orders =
CALCULATE ( [Orders], fact_orders[is_delivered] = 1,
            CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) )
Late Orders =
CALCULATE ( [Orders], fact_orders[is_late] = 1,
            CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) )
Late Rate % = DIVIDE ( [Late Orders], [Delivered Orders] )
Late Rate Target % = 0.08          -- agree the real target with the stakeholder
Late Rate vs Target (pp) = ( [Late Rate %] - [Late Rate Target %] ) * 100
Avg Delivery Days =
CALCULATE ( AVERAGE ( fact_orders[delivery_days] ), fact_orders[is_delivered] = 1,
            CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) )
Avg Promised Days =
CALCULATE ( AVERAGE ( fact_orders[promised_days] ), fact_orders[is_delivered] = 1,
            CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) )
Avg Days Late (when late) =
CALCULATE ( AVERAGE ( fact_orders[delay_days] ), fact_orders[is_late] = 1,
            CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) )

-- Reviews -------------------------------------------------------------------
Avg Review =
CALCULATE ( AVERAGE ( fact_orders[review_score] ),
            CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) )
Avg Review Late = CALCULATE ( [Avg Review], fact_orders[is_late] = 1 )
Avg Review On Time = CALCULATE ( [Avg Review], fact_orders[is_late] = 0 )
Review Gap (On time - Late) = [Avg Review On Time] - [Avg Review Late]
% 1-2 Star =
DIVIDE (
    CALCULATE ( [Orders], fact_orders[review_score] <= 2,
                CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) ),
    CALCULATE ( [Orders], NOT ISBLANK ( fact_orders[review_score] ),
                CROSSFILTER ( fact_order_items[order_id], fact_orders[order_id], BOTH ) )
)

-- Sellers & categories ---------------------------------------------------------
Seller Revenue Rank =
IF ( HASONEVALUE ( dim_seller[seller_id] ),
     RANKX ( ALL ( dim_seller[seller_id] ), [Revenue], , DESC, DENSE ) )
Category Revenue Share % =
DIVIDE ( [Revenue], CALCULATE ( [Revenue], ALL ( fact_order_items[category] ) ) )
Category Cumulative Share % =
VAR CurRev = [Revenue]
RETURN
    DIVIDE (
        SUMX ( FILTER ( ALL ( fact_order_items[category] ), [Revenue] >= CurRev ), [Revenue] ),
        CALCULATE ( [Revenue], ALL ( fact_order_items[category] ) )
    )
Sellers (30+ orders) =
COUNTROWS ( FILTER ( VALUES ( dim_seller[seller_id] ), [Orders] >= 30 ) )
```

---

## 3. Pages

All pages share three slicers in the top bar: **date range** (`dim_date[Date]`, default = trend window 2017-01 → 2018-08), **customer state** (`dim_customer[customer_state]`), **category** (`fact_order_items[category]`). Sync them across pages (View → Sync slicers).

### Page 1 — Overview
- **KPI cards:** Revenue, Orders, AOV, Revenue MoM %, Late Rate %, Avg Review, Repeat Rate %.
- **Line + column:** Revenue (columns) and Revenue 3M Avg (line) by `dim_date[Month]`.
- **Line:** AOV and Items per Order by month (answers "more orders or bigger baskets?").
- **Bar:** Revenue by customer state, sorted, with Revenue share as tooltip.
- Text box: one sentence stating the revenue definition (product price, excludes freight, excludes canceled/unavailable).

### Page 2 — Delivery performance
- **KPI cards:** Late Rate %, Late Rate vs Target (pp), Avg Delivery Days, Avg Promised Days, Review Gap.
- **Line:** Late Rate % by month with Late Rate Target % as a constant line.
- **Line:** Avg Delivery Days vs Avg Promised Days by month.
- **Bar:** Late Rate % by customer state (conditional colour above target).
- **Matrix:** seller state (rows) × customer state (columns) → Late Rate % with background colour scale; this is the "lanes" view. Seller state comes from `dim_seller`.
- **Column:** Avg Review and % 1-2 Star by Delay Bucket — the money chart.

### Page 3 — Sellers & categories
- **Table:** sellers with Seller Revenue Rank, Revenue, Orders, Late Rate %, Avg Review; visual-level filter Orders ≥ 30; conditional formatting on Late Rate %.
- **Scatter:** sellers, x = Revenue, y = Late Rate %, size = Orders. Top-right quadrant = high-revenue sellers breaking promises → call list.
- **Bar + line (Pareto):** Revenue by category with Category Cumulative Share % line.
- **Bar:** Late Rate % and Avg Review by category (categories with ≥ 200 delivered orders).

### Finishing
- Theme: one accent colour; late/negative in a single warning colour.
- Titles phrased as answers, e.g. "SP→RJ lane is late 2× as often as SP→SP".
- Export screenshots of each page to `dashboard/screenshots/01_overview.png`, `02_delivery.png`, `03_sellers_categories.png` and reference them in the README.
- Save as `dashboard/olist.pbix`.
