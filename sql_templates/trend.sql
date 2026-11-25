SELECT fiscal_year,
       month_name,
       SUM(netweight_tmt) AS total_sales_tmt
FROM {{source_table}}
WHERE {{filters}}
GROUP BY fiscal_year, month_name
ORDER BY fiscal_year, MIN(yearmonth);
