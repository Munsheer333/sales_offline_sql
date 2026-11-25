# NLPSQL Enterprise Benchmark Report
Generated on: 2026-06-11 11:14:12

This report summarizes the compliance, correctness, and performance of the NLPSQL deterministic query generation system.

## 1. Summary Metrics
| Metric / Check | Accuracy / Score | Description / Formula |
| --- | --- | --- |
| **Total Queries** | 1202 | Total number of unique benchmark cases |
| **Intent Accuracy** | **100.0%** | Correct routing class matching |
| **Entity Accuracy** | **100.0%** | Strict dimension filter value matches |
| **Planner Accuracy** | **99.5%** | Logical table, group-by, and join planning |
| **SQL Syntactic Accuracy** | **97.25%** | Exact match with reference compiler SQL |
| **Execution Accuracy** | **100.0%** | Direct cell-by-cell database output matching |
| **Business Accuracy** | **100.0%** | Compliance with all AST & MNC business rules |
| **Average Latency** | **84.68 ms** | End-to-end response time |

## 2. Category Accuracy Breakdown
| Category | Total | Intent | Entity | Planner | SQL Match | Exec Correct | Business OK |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **ACTUAL_VOLUME** | 95 | 100.0% | 100.0% | 96.8% | 100.0% | 100.0% | 100.0% |
| **MARKET_SHARE** | 76 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| **GROWTH** | 50 | 100.0% | 100.0% | 100.0% | 72.0% | 100.0% | 100.0% |
| **TOP_N** | 30 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| **TOTAL_SALES** | 881 | 100.0% | 100.0% | 99.8% | 99.7% | 100.0% | 100.0% |
| **COMPARISON** | 49 | 100.0% | 100.0% | 98.0% | 67.3% | 100.0% | 100.0% |
| **TREND** | 14 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| **SALES_DIFFERENCE** | 5 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| **BOTTOM_N** | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## 3. Sample Case Details
Below are representative evaluation cases detailing output correctness.

### Case 1: What is the actual sales in TMT for Retail HSD from Dec 1 2025 to Dec 15 2025?
- **Intent Correct**: `True` (Expected: `actual_volume`, Got: `actual_volume`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `116.76 ms`
- **Generated SQL**:
  ```sql
  SELECT ROUND(SUM(mom_level.netweight_tmt)::numeric, 2) AS sum_actual_sales_tmt FROM mom_level WHERE mom_level.sbu_name != '0' AND mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref') AND mom_level.productname = 'HSD' AND mom_level.sbu_name = 'Retail' AND mom_level.day_id BETWEEN '2025-12-01' AND '2025-12-15';
  ```

### Case 2: Show actual sales for product MS in MT for Retail in Oct 2025.
- **Intent Correct**: `True` (Expected: `actual_volume`, Got: `actual_volume`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `75.28 ms`
- **Generated SQL**:
  ```sql
  SELECT ROUND(SUM((mom_level.netweight_tmt * 1000))::numeric, 2) AS sum_actual_sales_mt FROM mom_level WHERE mom_level.sbu_name != '0' AND mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref') AND mom_level.productname = 'MS' AND mom_level.sbu_name = 'Retail' AND mom_level.yearmonth = 202510;
  ```

### Case 3: What is market share for IOCL in FY 2024-2025?
- **Intent Correct**: `True` (Expected: `market_share`, Got: `market_share`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `74.59 ms`
- **Generated SQL**:
  ```sql
  WITH company_sales AS (SELECT SUM(industry_performance.netweight_tmt) AS company_total FROM industry_performance WHERE (industry_performance.category != 'O' OR industry_performance.category IS NULL) AND industry_performance.comname = 'IOCL' AND industry_performance.fiscal_year = '2024-2025'), total_market AS (SELECT SUM(industry_performance.netweight_tmt) AS market_total FROM industry_performance WHERE (industry_performance.category != 'O' OR industry_performance.category IS NULL) AND industry_performance.fiscal_year = '2024-2025') SELECT ROUND((company_sales.company_total * 100.0) / NULLIF(total_market.market_total, 0), 2) AS market_share_percentage FROM company_sales CROSS JOIN total_market;
  ```

### Case 4: Which company has the lowest market share in fiscal year 2024-2025?
- **Intent Correct**: `True` (Expected: `market_share`, Got: `market_share`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `182.12 ms`
- **Generated SQL**:
  ```sql
  WITH company_sales AS (SELECT industry_performance.comname, SUM(industry_performance.netweight_tmt) AS company_total FROM industry_performance WHERE (industry_performance.category != 'O' OR industry_performance.category IS NULL) AND industry_performance.fiscal_year = '2024-2025' GROUP BY industry_performance.comname), total_market AS (SELECT SUM(industry_performance.netweight_tmt) AS market_total FROM industry_performance WHERE (industry_performance.category != 'O' OR industry_performance.category IS NULL) AND industry_performance.fiscal_year = '2024-2025') SELECT company_sales.comname, ROUND((company_sales.company_total * 100.0) / NULLIF(total_market.market_total, 0), 2) AS market_share_percentage FROM company_sales CROSS JOIN total_market ORDER BY market_share_percentage ASC LIMIT 1;
  ```

### Case 5: What is the sales growth percentage for HPCL in Retail SBU from FY 2023-2024 to FY 2024-2025?
- **Intent Correct**: `True` (Expected: `growth`, Got: `growth`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `False`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `102.38 ms`
- **Generated SQL**:
  ```sql
  WITH y1 AS (SELECT SUM(netweight_tmt) AS s1 FROM industry_performance WHERE (category != 'O' OR category IS NULL) AND comname = 'HPCL' AND sbu_name = 'Retail' AND fiscal_year = '2023-2024'), y2 AS (SELECT SUM(netweight_tmt) AS s2 FROM industry_performance WHERE (category != 'O' OR category IS NULL) AND comname = 'HPCL' AND sbu_name = 'Retail' AND fiscal_year = '2024-2025') SELECT ROUND(((y2.s2 - y1.s1) * 100.0) / y1.s1, 2) AS sales_growth_percentage FROM y1, y2;
  ```

### Case 6: Show products having growth of more than 5% in actual volume in current fiscal year.
- **Intent Correct**: `True` (Expected: `actual_volume`, Got: `actual_volume`)
- **Entities Match**: `True`
- **Planner Match**: `False`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `189.86 ms`
- **Generated SQL**:
  ```sql
  SELECT productname, ROUND((SUM(netweight_tmt) - SUM(target_qty_tmt)) * 100.0 / SUM(target_qty_tmt), 2) AS growth_percentage FROM m60_level WHERE fiscal_year = '2025-2026' GROUP BY productname HAVING ROUND((SUM(netweight_tmt) - SUM(target_qty_tmt)) * 100.0 / SUM(target_qty_tmt), 2) > 5;
  ```

### Case 7: Rank companies by industry performance sales in AP state.
- **Intent Correct**: `True` (Expected: `top_n`, Got: `top_n`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `113.7 ms`
- **Generated SQL**:
  ```sql
  SELECT industry_performance.comname, ROUND(SUM(industry_performance.netweight_tmt)::numeric, 2) AS sum_actual_sales_tmt FROM industry_performance WHERE (industry_performance.category != 'O' OR industry_performance.category IS NULL) AND industry_performance.statename = 'ANDHRA PRADESH' GROUP BY industry_performance.comname ORDER BY sum_actual_sales_tmt DESC;
  ```

### Case 8: Get the top 3 products by actual weight in current fiscal year.
- **Intent Correct**: `True` (Expected: `top_n`, Got: `top_n`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `133.65 ms`
- **Generated SQL**:
  ```sql
  SELECT mom_level.productname, ROUND(SUM(mom_level.netweight_tmt)::numeric, 2) AS sum_actual_sales_tmt FROM mom_level WHERE mom_level.sbu_name != '0' AND mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref') AND mom_level.fiscal_year = '2025-2026' GROUP BY mom_level.productname ORDER BY sum_actual_sales_tmt DESC LIMIT 3;
  ```

### Case 9: Get the total target quantity in KL from M60_LEVEL for strategic business unit I&C in North zone.
- **Intent Correct**: `True` (Expected: `total_sales`, Got: `total_sales`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `True`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `169.96 ms`
- **Generated SQL**:
  ```sql
  SELECT ROUND(SUM(m60_level.target_qty_kl)::numeric, 2) AS sum_target_kl FROM m60_level WHERE m60_level.sbu_name = 'I&C' AND m60_level.zone_name = 'North';
  ```

### Case 10: Compare HPCL and IOCL sales in West zone for FY 2024-2025.
- **Intent Correct**: `True` (Expected: `comparison`, Got: `comparison`)
- **Entities Match**: `True`
- **Planner Match**: `True`
- **SQL Match**: `False`
- **Execution Correct**: `True`
- **Business Rule Compliant**: `True`
- **Latency**: `52.0 ms`
- **Generated SQL**:
  ```sql
  SELECT comname, ROUND(SUM(netweight_tmt)::numeric, 2) AS total_sales FROM industry_performance WHERE zone_name = 'West' AND fiscal_year = '2024-2025' AND comname IN ('HPCL', 'IOCL') GROUP BY comname;
  ```
