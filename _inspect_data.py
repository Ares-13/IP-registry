import os
from dotenv import load_dotenv
import psycopg2

os.environ["PGCLIENTENCODING"] = "UTF8"
load_dotenv(r"D:\ip_reg\.env")
conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname="ip_reg",
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    client_encoding="UTF8",
)
cur = conn.cursor()
cur.execute("SELECT * FROM workstations ORDER BY id")
print("WS_COLS", [d[0] for d in cur.description])
print("WS_ROWS", cur.fetchall())
cur.execute("SELECT * FROM workstations_history ORDER BY history_id")
print("HIST_COLS", [d[0] for d in cur.description])
print("HIST_ROWS", cur.fetchall())
cur.execute(
    """
    SELECT column_name, character_maximum_length
    FROM information_schema.columns
    WHERE table_name='workstations_history' AND column_name='operation'
    """
)
print("OP_LEN", cur.fetchall())
conn.close()
