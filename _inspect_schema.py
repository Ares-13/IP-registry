import os
from dotenv import load_dotenv
import psycopg2

os.environ["PGCLIENTENCODING"] = "UTF8"
load_dotenv(r"D:\ip_reg\.env")
print("ENV", {k: os.getenv(k) for k in ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER"]})
try:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname="ip_reg",
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        client_encoding="WIN1251",
    )
except Exception as e:
    print("CONNECT_ERR", type(e), repr(e))
    raise
cur = conn.cursor()
cur.execute("SELECT datname FROM pg_database ORDER BY 1")
print("DATABASES", cur.fetchall())
cur.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1"
)
print("TABLES", cur.fetchall())
cur.execute(
    """
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='workstations'
    ORDER BY ordinal_position
    """
)
print("WORKSTATIONS", cur.fetchall())
cur.execute(
    """
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_schema='public' AND table_name='workstations_history'
    ORDER BY ordinal_position
    """
)
print("HISTORY", cur.fetchall())
cur.execute("SELECT COUNT(*) FROM workstations")
print("WS_COUNT", cur.fetchone())
try:
    cur.execute("SELECT COUNT(*) FROM workstations_history")
    print("HIST_COUNT", cur.fetchone())
except Exception as e:
    print("HIST_ERR", e)
cur.execute(
    """
    SELECT conname, pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conrelid = 'public.workstations'::regclass
    """
)
print("WS_CONS", cur.fetchall())
try:
    cur.execute(
        """
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'public.workstations_history'::regclass
        """
    )
    print("HIST_CONS", cur.fetchall())
except Exception as e:
    print("HIST_CONS_ERR", e)
conn.close()
