import os
import sqlite3
from datetime import date
from pathlib import Path

from ledger.models import Expense

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    day TEXT NOT NULL
)
"""


def connect(path=None):
    path = path or os.environ.get("LEDGER_DB") or Path.home() / ".ledger.db"
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    return conn


def add(conn, expense):
    cur = conn.execute("INSERT INTO expenses (amount, category, note, day) VALUES (?, ?, ?, ?)",
                       (expense.amount, expense.category, expense.note, expense.day.isoformat()))
    conn.commit()
    return cur.lastrowid


def month_range(month):
    """'2026-09' -> ('2026-09-01', '2026-10-01')"""
    year, m = map(int, month.split("-"))
    start = date(year, m, 1)
    end = date(year, m + 1, 1)
    return start.isoformat(), end.isoformat()


def expenses(conn, month=None, category=None):
    query, args = "SELECT id, amount, category, note, day FROM expenses WHERE 1=1", []
    if month:
        start, end = month_range(month)
        query += " AND day >= ? AND day < ?"
        args += [start, end]
    if category:
        query += " AND category = ?"
        args.append(category)
    rows = conn.execute(query + " ORDER BY day, id", args).fetchall()
    return [Expense(amount=a, category=c, note=n, day=date.fromisoformat(d), id=i) for i, a, c, n, d in rows]


def summary(conn, month):
    totals = {}
    for e in expenses(conn, month):
        totals[e.category] = totals.get(e.category, 0) + e.amount
    return totals
