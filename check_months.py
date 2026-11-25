from config import DB_URL
from core.db_pool import pooled_connect

def check_months():
    conn = pooled_connect(DB_URL)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT month_name FROM industry_performance LIMIT 15")
        print("month_name in industry_performance:", cur.fetchall())
        
        # Let's see if there is any mapping of month to order
        cur.execute("SELECT DISTINCT month_name, code, newcode FROM industry_performance LIMIT 15")
        print("month_name, code, newcode:")
        for r in cur.fetchall():
            print(r)

if __name__ == "__main__":
    check_months()
