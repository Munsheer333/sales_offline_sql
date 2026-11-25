import psycopg2
from config import DB_URL

try:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Let's run a simple check to see what tables exist
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
    print("Tables:", cur.fetchall())
    
    # Try running the TREND SQL
    sql = "SELECT ROUND(SUM(mom_level.netweight_tmt)::numeric, 2) AS sum_actual_sales_tmt FROM mom_level WHERE mom_level.sbu_name != '0' AND mom_level.sbu_name NOT IN ('Common', 'Mumbai Ref', 'Renewable Energy', 'Visakh Ref') AND mom_level.productname = 'LPG' AND mom_level.fiscal_year = '2021-2022' AND mom_level.sbu_name IN ('Retail', 'LPG');"
    print("Executing query...")
    cur.execute(sql)
    print("Result:", cur.fetchall())
    
    cur.close()
    conn.close()
except Exception as e:
    print("Error:", e)
    import traceback
    traceback.print_exc()
