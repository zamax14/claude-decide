from dataclasses import dataclass
from datetime import date

CATEGORIES = ("food", "transport", "home", "health", "leisure", "other")


@dataclass
class Expense:
    amount: float
    category: str
    note: str = ""
    day: date = None
    id: int = None

    def __post_init__(self):
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}; use one of {', '.join(CATEGORIES)}")
        self.day = self.day or date.today()
