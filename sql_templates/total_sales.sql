SELECT {{dimensions_select}}
       SUM(netweight_tmt) AS total_sales_tmt
FROM {{source_table}}
WHERE {{filters}}
{{dimensions_group_by}};
