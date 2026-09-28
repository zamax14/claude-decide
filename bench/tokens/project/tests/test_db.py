import sqlite3
import unittest
from datetime import date

from ledger import db
from ledger.models import Expense


class DbTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_add_and_filter_by_month(self):
        db.add(self.conn, Expense(10, "food", "lunch", date(2026, 9, 3)))
        db.add(self.conn, Expense(5, "transport", "bus", date(2026, 8, 30)))
        self.assertEqual([e.note for e in db.expenses(self.conn, "2026-09")], ["lunch"])

    def test_summary_by_category(self):
        for amount in (10, 2.5):
            db.add(self.conn, Expense(amount, "food", day=date(2026, 9, 1)))
        self.assertEqual(db.summary(self.conn, "2026-09"), {"food": 12.5})

    def test_rejects_bad_expenses(self):
        with self.assertRaises(ValueError):
            Expense(-1, "food")
        with self.assertRaises(ValueError):
            Expense(1, "pets")
