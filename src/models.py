"""
models.py - Data model definitions

This file defines the Expense dataclass used across the tracker and UI.
Expenses are serialized to/from simple dicts so they can be persisted as JSON
in data/expenses_data.json.
"""

from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class Expense:
    """
    Represents a single household expense.

    Fields:
      - id: integer unique id assigned by the tracker (for display/reference)
      - amount: numeric total amount of the expense (float, currency implied by `unit`)
      - payer: who paid (restricted in UI to "Alessio" or "Morgan")
      - participants: list of names who share the expense (subset of household)
      - category: expense category (e.g. Groceries); tracker persists category list
      - description: optional free-text description
      - unit: currency/unit string (e.g. "EUR", "USD"); tracker groups balances by unit
      - shares: optional mapping participant -> amount (useful for custom splits)
      - date: ISO date string "YYYY-MM-DD" (mandatory in UI)
    """
    id: int = field(default=0)
    amount: float = 0.0
    payer: str = ""
    participants: List[str] = field(default_factory=list)
    category: str = "general"
    description: str = ""
    unit: str = "EUR"
    shares: Dict[str, float] = field(default_factory=dict)
    date: str = ""  # stored as ISO "YYYY-MM-DD"

    def to_dict(self) -> Dict:
        """
        Convert to a plain dict suitable for JSON serialization.
        The tracker writes lists of these dicts to the data file.
        """
        return {
            "id": self.id,
            "amount": self.amount,
            "payer": self.payer,
            "participants": self.participants,
            "category": self.category,
            "description": self.description,
            "unit": self.unit,
            "shares": self.shares,
            "date": self.date,
        }

    @staticmethod
    def from_dict(d: Dict) -> "Expense":
        """
        Construct an Expense from a dict (inverse of to_dict).
        Uses defaults for missing keys so older/corrupted files are tolerated.
        """
        return Expense(
            id=d.get("id", 0),
            amount=d.get("amount", 0.0),
            payer=d.get("payer", ""),
            participants=d.get("participants", []) or [],
            category=d.get("category", "general"),
            description=d.get("description", ""),
            unit=d.get("unit", "EUR"),
            shares=d.get("shares", {}) or {},
            date=d.get("date", "") or "",
        )