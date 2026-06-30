"""
Tests for the Turso DB adapter in utils.py.
Run with: pytest test_utils.py -v
"""
import datetime
import numpy as np
import pandas as pd
from utils import TursoCursor, _to_native, db_read_sql, db_to_sql


class FakeResultSet:
    """Mimics libsql_client's ResultSet shape used by TursoCursor."""
    def __init__(self, columns, rows, last_insert_rowid=0, rows_affected=0):
        self.columns = columns
        self.rows = rows
        self.last_insert_rowid = last_insert_rowid
        self.rows_affected = rows_affected


class FakeConnection:
    """Mimics TursoConnection — records every execute() call instead of hitting Turso."""
    def __init__(self, result_set=None):
        self._result_set = result_set or FakeResultSet([], [])
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return TursoCursor(self._result_set)


# ── TursoCursor ────────────────────────────────────────────────────────────────

def test_cursor_fetchone_returns_rows_in_order():
    rs = FakeResultSet(columns=["id", "name"], rows=[(1, "a"), (2, "b")])
    cur = TursoCursor(rs)
    assert cur.fetchone() == (1, "a")
    assert cur.fetchone() == (2, "b")
    assert cur.fetchone() is None


def test_cursor_fetchall_returns_all_then_empties():
    rs = FakeResultSet(columns=["id"], rows=[(1,), (2,), (3,)])
    cur = TursoCursor(rs)
    assert cur.fetchall() == [(1,), (2,), (3,)]
    assert cur.fetchall() == []


# ── _to_native ─────────────────────────────────────────────────────────────────

def test_to_native_converts_nan_to_none():
    assert _to_native(float("nan")) is None


def test_to_native_converts_none_to_none():
    assert _to_native(None) is None


def test_to_native_converts_numpy_int_to_python_int():
    value = _to_native(np.int64(42))
    assert value == 42
    assert isinstance(value, int)


def test_to_native_converts_timestamp_to_isoformat_string():
    ts = pd.Timestamp("2026-06-28 10:30:00")
    assert _to_native(ts) == ts.isoformat()


def test_to_native_passes_through_plain_values():
    assert _to_native("hello") == "hello"
    assert _to_native(5) == 5


# ── db_read_sql / db_to_sql ─────────────────────────────────────────────────────

def test_db_read_sql_builds_dataframe_from_cursor():
    rs = FakeResultSet(columns=["temp", "humidity"], rows=[(28.5, 82.0)])
    conn = FakeConnection(rs)
    df = db_read_sql("SELECT temp, humidity FROM sensors", conn)
    assert list(df.columns) == ["temp", "humidity"]
    assert df.iloc[0]["temp"] == 28.5


def test_db_to_sql_inserts_one_row_per_dataframe_row():
    conn = FakeConnection()
    df = pd.DataFrame({"id": [1, 2], "temp": [28.5, float("nan")]})
    db_to_sql(df, "sensors", conn)
    assert len(conn.calls) == 2
    sql, params = conn.calls[0]
    assert "INSERT INTO sensors" in sql
    assert params == [1, 28.5]
    # NaN must become None, not be sent as float('nan') which Turso/JSON can't encode
    _, second_params = conn.calls[1]
    assert second_params == [2, None]


def test_db_to_sql_skips_empty_dataframe():
    conn = FakeConnection()
    db_to_sql(pd.DataFrame(), "sensors", conn)
    assert conn.calls == []
