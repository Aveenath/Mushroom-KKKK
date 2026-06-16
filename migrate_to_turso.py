"""One-time script: copy schema + data from local mushroom_client.db to Turso.

Run once with: python migrate_to_turso.py
Safe to delete afterwards.
"""
import sqlite3
import pandas as pd
from utils import get_db_connection, db_to_sql

LOCAL_DB = "mushroom_client.db"
TABLES = ["users", "situation_reports", "planting_records", "ai_harvest_logs", "sensors"]


def main():
    local = sqlite3.connect(LOCAL_DB)
    conn = get_db_connection()

    # Clean up the table created while testing Turso connectivity
    conn.execute("DROP TABLE IF EXISTS test_table")

    # 1. Recreate schema (CREATE TABLE / CREATE INDEX statements as they
    #    currently exist locally, including any ALTER TABLE additions)
    schema = local.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    schema.sort(key=lambda row: row[0] != 'table')  # tables before indexes

    for obj_type, name, sql in schema:
        if not sql:
            continue
        try:
            conn.execute(sql)
            print(f"Created {obj_type}: {name}")
        except Exception as e:
            print(f"Skipped {obj_type} {name} (already exists?): {e}")

    # 2. Copy data
    for table in TABLES:
        df = pd.read_sql(f"SELECT * FROM {table}", local)
        existing = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if existing > 0:
            print(f"{table}: Turso already has {existing} row(s), skipping copy.")
            continue
        db_to_sql(df, table, conn, if_exists="append")
        print(f"{table}: copied {len(df)} row(s).")

    local.close()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    main()
