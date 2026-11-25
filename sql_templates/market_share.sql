WITH scoped_sales AS (
    SELECT {{dimensions_select}}
           SUM(netweight_tmt) AS scoped_total_tmt
    FROM {{source_table}}
    WHERE {{filters}}
    {{dimensions_group_by}}
),
market_sales AS (
    SELECT {{market_dimensions_select}}
           SUM(netweight_tmt) AS market_total_tmt
    FROM {{source_table}}
    WHERE {{market_filters}}
    {{market_dimensions_group_by}}
)
SELECT {{dimensions_output}}
       ROUND((scoped_sales.scoped_total_tmt * 100.0) / NULLIF(market_sales.market_total_tmt, 0), 2)
           AS market_share_percentage
FROM scoped_sales
{{dimension_join}};
