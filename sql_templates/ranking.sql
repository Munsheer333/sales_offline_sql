SELECT {{dimensions_select}}
       SUM(netweight_tmt) AS total_sales_tmt
FROM {{source_table}}
WHERE {{filters}}
{{dimensions_group_by}}
ORDER BY total_sales_tmt {{order_direction}}
LIMIT {{limit}};
