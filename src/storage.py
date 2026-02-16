from dataclasses import dataclass, field
from typing import List
import json
import os

DATA_FILE = os.path.join(os.path.dirname(__file__), "../data/expenses_data.json")

@dataclass
class Expense:
    amount: float
    payer: str
    participants: List[str]
    category: str = "general"
    description: str = ""
    id: int = field(default=0)

    def to_dict(self):
        return {
            "id": self.id,
            "amount": self.amount,
            "payer": self.payer,
            "participants": self.participants,
            "category": self.category,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d):
        return Expense(
            amount=d["amount"],
            payer=d["payer"],
            participants=d["participants"],
            category=d.get("category", "general"),
            description=d.get("description", ""),
            id=d.get("id", 0),
        )


class Storage:
    @staticmethod
    def load_expenses() -> List[Expense]:
        if not os.path.exists(DATA_FILE):
            return []
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [Expense.from_dict(d) for d in data.get("expenses", [])]

    @staticmethod
    def save_expenses(expenses: List[Expense], next_id: int):
        data = {"next_id": next_id, "expenses": [e.to_dict() for e in expenses]}
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)