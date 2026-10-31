# Olist — findings

**Audience:** Operations Manager (seller logistics) · **Data:** Olist public dataset, 99,441 orders purchased 2016-09 → 2018-10 · **Trend window:** 2017-01 → 2018-08
**Definitions:** revenue = item price (BRL, excl. freight) on orders not canceled/unavailable; late = delivered on a later calendar date than the estimate shown at checkout; customer = `customer_unique_id`.
Every number cites the query that produced it (`reports/sql_outputs/<query>.csv`).

---

## Summary for the operations manager
1. **Lateness is the single biggest driver of bad reviews.** Late orders average **2.27★ vs 4.29★** on time, and **62%** of late orders get 1–2★ (vs 9%). Even 1–3 days late drops the average to 3.29★.
2. **Late deliveries are concentrated and fixable.** Two lanes, **SP→SP and SP→RJ, produce 40% of all late orders**. SP→RJ alone is 8.5% of deliveries but **17.6% of late orders** (14.1% late rate).
3. **The promise matters more than the distance.** When the checkout estimate is shorter than how long the lane has recently taken, **22% of orders arrive late**, versus 3% with 15+ days of slack. Orders with under 5 days of slack are 18% of volume but **34% of late orders**.
4. **Growth is volume, not basket size, and it stalled in 2018.** Orders grew 2.4× year on year while AOV stayed at 125–153 BRL. Monthly revenue has been flat at ~0.84–0.99M BRL since January 2018.
5. **Almost nobody comes back.** Only **2.2%** of customers buy again on a different day.

---

## Week 1 — SQL findings

### 1. Growth
- Monthly revenue grew from **120k BRL (2017-01) to 849k BRL (2018-08)**. The best month was **2017-11 at 1.00M BRL**: orders jumped 63% over October (7,421 vs 4,547), consistent with Black Friday. *(q1_monthly_revenue_growth, q1_best_and_worst_months)*
- Jan–Aug 2018 vs Jan–Aug 2017: revenue **7.34M vs 3.08M BRL (+138%)**, orders **53,530 vs 22,562 (2.4×)**. *(q1_monthly_revenue_growth)*
- **Growth came from more orders, not bigger baskets:** monthly AOV stayed between 125 and 153 BRL, and items per order between 1.11 and 1.21 throughout. *(q2_aov_monthly)*
- **2018 has plateaued:** every month from January to August sits between 0.84M and 0.99M BRL. YoY growth slowed from +687% (Jan) to +49% (Aug). *(q1_monthly_revenue_growth)*
- Mean order value **137 BRL**, median **87 BRL**. The top 10% of orders bring **41% of revenue**. *(q2_aov_overall, q2_order_value_deciles)*
- São Paulo state is **38%** of revenue; SP + RJ + MG together are **63%**. Average freight is 17 BRL to SP but 36–49 BRL to northern and north-eastern states. *(q2_revenue_by_customer_state)*
- **So what:** the 2018 plateau is the leadership question; growth has relied on new buyers, which the next finding shows is expensive.

### 2. Customers barely come back
- **3.04%** of 94,990 customers placed 2+ orders, and repeaters account for 6.2% of orders. *(q3_repeat_rate_overall)*
- **31%** of those "second orders" happen on the **same day** as the first, which means split baskets rather than loyalty. Counting only purchases on a different day, the repeat rate is **2.17%**. *(q3_time_to_second_purchase, q3_repeat_rate_excl_same_day)*
- The 90-day repeat rate for cohorts with a complete window ranges from **1.6% to 3.9%**. Month-1 retention is under 1% for every cohort. *(q3_repeat_within_90d_by_cohort, q3_cohort_retention_matrix)*
- **The identity trap:** using `customer_id` would show a repeat rate of **0%**, because no `customer_id` ever has two orders; there are 99,441 customer_ids but only 96,096 actual people. *(dq_customer_identity)*
- **So what:** late deliveries and 1★ reviews (finding 4) hit customers who already rarely return; delivery reliability is one of the few retention levers ops controls.

### 3. Revenue is concentrated
- The top 3 categories (**health_beauty, watches_gifts, bed_bath_table**) bring **25.9%** of revenue, and the top 10 bring **62.4%**. *(q4_category_revenue_pareto)*
- H1 2018 revenue was up **178%** on H1 2017 (2.02M → 5.61M BRL). watches_gifts (+301%) and health_beauty (+234%) alone supplied **22%** of that growth. *(q4_category_growth_h1)*
- The **top 10% of sellers (305 of 3,053) bring 67.5%** of revenue; the top 1% bring 26.2%. *(q4_seller_concentration)*
- Among the 25 largest sellers, late rates range from **3.4% to 13.8%** and average reviews from 3.49 to 4.42. The #1 seller by revenue runs at a 10.5% late rate. *(q4_top_sellers)*
- office_furniture has the worst average review (**3.64**) and the slowest delivery (**20.6 days** on average). *(q4_category_quality)*
- **So what:** a short list of large sellers and categories can move platform-wide delivery numbers; seller-level SLAs are worth targeting first.

### 4. Late deliveries and what they cost
- **6.77%** of 96,470 delivered orders arrive late, by **10.6 days** on average when late. The typical order arrives **11.9 days early**: 12.6 days actual vs 23.7 days promised. *(q5_delivery_summary)*
- Lateness comes in waves: about **3% per month for most of 2017**, then **12.4% in Nov 2017, 14.1% in Feb 2018 and 19.0% in Mar 2018**, falling back to **1.2% in Jun 2018**. *(q5_late_rate_monthly)*
- Worst destination states: **AL 21.4%, MA 17.4%, SE 15.2%**. RJ is only 12.1%, but on 12,350 orders. SP (4.5%) and PR (4.0%) are the most reliable. *(q5_late_rate_by_customer_state)*
- **Two lanes, SP→SP and SP→RJ, produce 39.5% of all late orders.** SP→RJ is late 14.1% of the time, three times SP→SP's 4.7%. The top 4 lanes produce 50%. *(q5_late_orders_by_route_share, q5_late_rate_by_route)*
- Where the time goes: late orders spend **27.9 days in carrier transit vs 8.0** for on-time orders, and **5.6 days in seller hand-over vs 2.6**. **27.8%** of late orders missed the seller's shipping-limit date, vs 7.7% of on-time orders. *(q5_delay_stage_breakdown)*
- **Reviews:** late orders average **2.27★ vs 4.29★** on time; **62.4%** of late orders get 1–2★ (vs 9.3%). The damage starts on day one: 1–3 days late → 3.29★, 4–7 days → 2.10★, 8+ days → about 1.7★. *(q6_review_late_vs_ontime, q6_review_vs_delay)*
- **So what:** fix the SP→RJ carrier lane, enforce seller shipping limits, and stop issuing promises shorter than the lane can deliver (see Week 2).

## Data quality notes
- **8** orders are marked delivered but have no delivery date; **6** have a delivery date without the delivered status. *(dq_timestamp_order_violations)*
- **1,359** orders show the carrier pickup before payment approval, and **23** show delivery before carrier pickup. These are known source quirks; stage-duration averages include them. *(dq_timestamp_order_violations)*
- **814** duplicate review_ids and **547** orders with more than one review; the latest review per order is used. *(dq_duplicates)*
- **610** products have no category, and 2 categories (13 products) have no English name. These are reported as `unknown` or under their Portuguese name. *(dq_product_gaps, dq_untranslated_categories)*
- **775** orders have no items, so they carry no revenue; 767 of them are canceled or unavailable. Payment totals match items + freight within 1 BRL for **99.7%** of orders. *(dq_orphans, dq_payment_vs_items)*
- 2016 and Sep–Oct 2018 are nearly empty (fewer than 400 orders a month), so trends use 2017-01 → 2018-08. *(dq_purchase_date_coverage)*

---

## Week 2 — model and dashboard

**Question:** at checkout, can we tell which orders will arrive late?

- **Main test (purchases 2018-06-21 → 2018-08-29, 14,485 orders, 4.28% late):** gradient boosting **PR-AUC 0.064** (1.5× the base rate), ROC-AUC 0.62; logistic regression PR-AUC 0.067, ROC-AUC 0.57. Flagging the riskiest **19%** of orders (a rate chosen on validation to reach 50% recall) catches **31%** of late orders at 6.9% precision. *(reports/model_metrics.json)*
- **Rolling backtest, four two-month windows:** gradient boosting PR-AUC ranges from **0.066 to 0.299**, always **1.8–2.5× the base rate**, with ROC-AUC 0.66–0.74. *(reports/backtest.csv)*
- **Why the main test is the hardest:** in mid-2018 Olist shortened its promises (average promised time fell from 27.8 days in June to 15.2 in August). That reversed the direction of several geography features: distance and freight predicted lateness in 2017 but not in Jul–Aug 2018. A random split hides this (ROC-AUC 0.81), which is exactly why the split is by date.
- **Strongest signal: slack**, the promised days minus the lane's average delivery time over the previous 60 days, using only orders already delivered at purchase. It is the top feature by permutation importance and the only one whose direction never changed across periods. *(reports/feature_importance.csv)*

| Slack at checkout | Share of orders | Late rate | Share of all late orders |
|---|---|---|---|
| < 0 days (promise shorter than the lane's recent average) | 3.5% | **22.3%** | 11.6% |
| 0–5 days | 14.8% | 10.3% | 22.4% |
| 5–10 days | 27.2% | 7.7% | 31.1% |
| 10–15 days | 30.0% | 5.2% | 23.1% |
| 15+ days | 24.5% | 3.3% | 11.8% |

- **Recommended use:** the model is a ranking aid, not a yes/no oracle. The simplest high-value action needs no model at all: **never issue a promise shorter than the lane's recent delivery time plus a buffer.** Orders under 5 days of slack are 18% of volume and 34% of late orders. Use the model's top 10–20% risk list for proactive customer messaging and seller follow-up, and retrain monthly, since the patterns drift.
