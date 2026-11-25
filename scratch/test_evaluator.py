import psycopg2
from config import DB_URL
from benchmark.evaluator import compare_db_results_smart

conn = psycopg2.connect(DB_URL)

sql = "SELECT ROUND(SUM(mom_level.netweight_tmt)::numeric, 2) AS sum_actual_sales_tmt FROM mom_level WHERE mom_level.sbu_name != '0' AND mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref') AND mom_level.productname = 'LPG' AND mom_level.fiscal_year = '2021-2022' AND mom_level.sbu_name IN ('Retail', 'LPG');"

print("Comparing same query:")
res = compare_db_results_smart(conn, sql, sql)
print("Result:", res)

conn.close()
