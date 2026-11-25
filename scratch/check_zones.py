import psycopg2
from config import DB_URL

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

cur.execute("SELECT DISTINCT zone_name FROM industry_performance;")
print("zone_name values in industry_performance:", cur.fetchall())

cur.execute("SELECT comname, round(sum(netweight_tmt)::numeric, 2) FROM industry_performance WHERE comname IN ('HPCL', 'IOCL') AND zone_name = 'West' AND fiscal_year = '2024-2025' GROUP BY comname;")
print("West results:", cur.fetchall())

cur.execute("SELECT comname, round(sum(netweight_tmt)::numeric, 2) FROM industry_performance WHERE comname IN ('HPCL', 'IOCL') AND zone_name = 'WZ' AND fiscal_year = '2024-2025' GROUP BY comname;")
print("WZ results:", cur.fetchall())

cur.close()
conn.close()
