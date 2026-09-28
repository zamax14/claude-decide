import csv
from datetime import date

from ledger.models import Expense


def from_csv(path):
    """Bank export with columns date,amount,category,note (amount negative for spending)."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            amount = -float(row["amount"])
            if amount <= 0:
                continue  # income
            out.append(Expense(amount=amount, category=row.get("category") or "other",
                               note=row.get("note", ""), day=date.fromisoformat(row["date"])))
    return out
