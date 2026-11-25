SELECT {{dimensions_select}}
       SUM(CASE WHEN fiscal_year = {{previous_year}} THEN netweight_tmt ELSE 0 END) AS previous_sales_tmt,
       SUM(CASE WHEN fiscal_year = {{current_year}} THEN netweight_tmt ELSE 0 END) AS current_sales_tmt,
       ROUND(
           (
               SUM(CASE WHEN fiscal_year = {{current_year}} THEN netweight_tmt ELSE 0 END)
               - SUM(CASE WHEN fiscal_year = {{previous_year}} THEN netweight_tmt ELSE 0 END)
           ) * 100.0
           / NULLIF(SUM(CASE WHEN fiscal_year = {{previous_year}} THEN netweight_tmt ELSE 0 END), 0),
           2
       ) AS growth_percentage
FROM {{source_table}}
WHERE {{filters}}
{{dimensions_group_by}};
