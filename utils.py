import os
import datetime
import libsql_client
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        url = os.environ["TURSO_DATABASE_URL"].replace("libsql://", "https://")
        token = os.environ["TURSO_AUTH_TOKEN"]
        _client = libsql_client.create_client_sync(url=url, auth_token=token)
    return _client


class TursoCursor:
    def __init__(self, result_set):
        self.columns = list(result_set.columns)
        self._rows = [tuple(row) for row in result_set.rows]
        self.lastrowid = result_set.last_insert_rowid
        self.rowcount = result_set.rows_affected

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class TursoConnection:
    def __init__(self, client):
        self._client = client

    def execute(self, sql, params=None):
        result = self._client.execute(sql, list(params) if params else None)
        return TursoCursor(result)

    def commit(self):
        pass

    def close(self):
        pass


def get_db_connection():
    return TursoConnection(_get_client())


def get_local_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def _to_native(value):
    """Convert pandas/numpy scalars to plain Python types for Turso."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def db_read_sql(query, conn, params=None):
    """pd.read_sql replacement for TursoConnection."""
    cur = conn.execute(query, params)
    return pd.DataFrame(cur.fetchall(), columns=cur.columns)


def db_to_sql(df, table, conn, if_exists="append"):
    """df.to_sql replacement for TursoConnection (append-only)."""
    if if_exists != "append":
        raise NotImplementedError("db_to_sql only supports if_exists='append'")
    if df.empty:
        return

    cols = ", ".join(f'"{c}"' for c in df.columns)
    placeholders = ", ".join("?" for _ in df.columns)
    insert_sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    for row in df.itertuples(index=False, name=None):
        conn.execute(insert_sql, [_to_native(v) for v in row])
