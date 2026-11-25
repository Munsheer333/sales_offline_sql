SELECT {{dimensions_select}}
       ROUND(SUM(netweight_tmt)::numeric, 2) AS total_sales_tmt
FROM {{source_table}}
WHERE {{filters}}
{{dimensions_group_by}}
{{order_by}};
