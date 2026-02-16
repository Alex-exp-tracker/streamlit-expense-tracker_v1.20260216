"""
tracker.py - core application logic and persistence

Responsibilities:
 - keep an in-memory list of Expense objects
 - persist/load data to data/expenses_data.json (expenses, categories, next_id)
 - provide helper APIs consumed by the UI:
     add_expense, list_expenses(filter by year/month),
     balances (grouped by currency), settle_suggestions (per currency),
     totals_by_month/year, category management
"""

from typing import List, Dict, Optional, Tuple
import sys
import tempfile
from src.models import Expense
import json
import os
import datetime
from collections import defaultdict
import tempfile
import shutil
import logging

# Optional Google Sheets backend imports are lazy/optional; we try to use them
try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

# location of the JSON persistence file (relative to src/)
_default_data_file = os.path.join(os.path.dirname(__file__), "..", "data", "expenses_data.json")
# When running under pytest, use a temp file to avoid reading/writing the project's
# real data file and to keep tests isolated from user data.
if any("pytest" in p for p in sys.argv) or os.getenv("PYTEST_CURRENT_TEST"):
    DATA_FILE = os.path.join(tempfile.gettempdir(), "tmp_expenses_test.json")
else:
    DATA_FILE = _default_data_file

# default categories shown when the data file has no categories saved yet
DEFAULT_CATEGORIES = [
    "Groceries",
    "MiniM",
    "Electricity & Water",
    "Eating out",
    "Fuel",
    "Home",
    "Electronics",
    "Telephony",
    "Taxes",
    "PPE",
    "Culture & Entertainment",
    "Transport",
    "Gifts",
    "Clothes",
    "Airfares",
    "Hotels - Holidays",
    "Fuel - Holidays",
    "Culture & Entertainment - Hol.",
    "Transport - Holidays",
]

# ensure a logger is available
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class ExpenseTracker:
    """
    Single-instance style tracker object. The UI creates one ExpenseTracker()
    and uses its methods to read/write data.
    """

    def __init__(self):
        # in-memory list of Expense objects
        self.expenses: List[Expense] = []
        # category list persisted alongside expenses
        self.categories: List[str] = list(DEFAULT_CATEGORIES)
        # next id for new expenses
        self._next_id = 1
        # load persisted state (if any)
        # initialize Google Sheets backend (if configured)
        self._gs_backend = GoogleSheetsBackend() if "GoogleSheetsBackend" in globals() else None
        # When running tests, ensure we start from a clean state by removing
        # any temp data file left from previous test runs.
        if any("pytest" in p for p in sys.argv) or os.getenv("PYTEST_CURRENT_TEST"):
            try:
                if os.path.exists(DATA_FILE):
                    os.remove(DATA_FILE)
            except Exception:
                pass
        self.load()

    def add_expense(
        self,
        amount: float,
        payer: str,
        participants: List[str],
        category: str = "general",
        description: str = "",
        unit: str = "EUR",
        shares: Dict[str, float] = None,
        date: str = "",
    ) -> Expense:
        """
        Create an Expense, append it to the in-memory list and persist.
        shares: optional per-participant amounts (used for custom split).
        date: ISO string "YYYY-MM-DD" (UI ensures valid date).
        """
        if shares is None:
            shares = {}
        exp = Expense(
            id=self._next_id,
            amount=amount,
            payer=payer,
            participants=participants,
            category=category,
            description=description,
            unit=unit,
            shares=shares,
            date=date,
        )
        self._next_id += 1
        self.expenses.append(exp)
        self.save()
        return exp

    def get_categories(self) -> List[str]:
        """Return a copy of the category list used to populate dropdowns in the UI."""
        return list(self.categories)

    def add_category(self, name: str) -> bool:
        """
        Persist a new category if it doesn't already exist.
        Returns True when a new category was added, False otherwise.
        """
        name = (name or "").strip()
        if not name:
            return False
        if name in self.categories:
            return False
        self.categories.append(name)
        self.save()
        return True

    def list_expenses(self, year: Optional[int] = None, month: Optional[int] = None) -> List[Expense]:
        """
        Return the list of expenses, optionally filtered by year and/or month.
        Date strings in Expense.date are parsed with datetime.date.fromisoformat.
        Invalid or missing dates are skipped when filtering.
        """
        if year is None and month is None:
            return list(self.expenses)
        out: List[Expense] = []
        for e in self.expenses:
            if not e.date:
                continue
            try:
                d = datetime.date.fromisoformat(e.date)
            except Exception:
                # tolerate bad dates in data file
                continue
            if year is not None and d.year != year:
                continue
            if month is not None and d.month != month:
                continue
            out.append(e)
        return out

    def clear(self):
        """
        Reset tracker state: clear expenses, reset categories to default and next_id.
        Persists the cleared state.
        """
        self.expenses = []
        self.categories = list(DEFAULT_CATEGORIES)
        self._next_id = 1
        self.save()

    def save(self):
        """
        Persist tracker state as JSON atomically.
        Logs the target path so we can verify the file being written.
        """
        data = {
            "next_id": self._next_id,
            "expenses": [e.to_dict() for e in self.expenses],
            "categories": self.categories,
        }

        # If Google Sheets backend is configured and available, use it.
        try:
            if getattr(self, "_gs_backend", None) and self._gs_backend.available:
                logger.info("Saving data to Google Sheets (expenses=%d)", len(self.expenses))
                ok = self._gs_backend.save_state(data)
                if ok:
                    return
                else:
                    logger.warning("Google Sheets save failed, falling back to local JSON")
        except Exception:
            logger.exception("Error while attempting to save to Google Sheets; falling back to local JSON")

        # Fallback to local JSON file
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        target = os.path.abspath(DATA_FILE)
        logger.info(f"Saving data to {target} (expenses={len(self.expenses)})")
        # atomic write: write to temp file then move
        dirn = os.path.dirname(target)
        fd, tmp_path = tempfile.mkstemp(prefix="tmp_expenses_", dir=dirn, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            shutil.move(tmp_path, target)
        except Exception as exc:
            logger.exception("Failed to save data file")
            # try to remove tmp file if present
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise

    def load(self):
        """
        Load tracker state from JSON. If the file does not exist, nothing is loaded
        and defaults remain (empty expenses, default categories).
        After loading we reindex expenses sequentially and update _next_id so
        numbering is always contiguous (1..n).
        """
        # Try Google Sheets backend first
        try:
            if getattr(self, "_gs_backend", None) and self._gs_backend.available:
                logger.info("Loading data from Google Sheets")
                data = self._gs_backend.load_state() or {}
            else:
                data = None
        except Exception:
            logger.exception("Error loading from Google Sheets, falling back to local JSON")
            data = None

        if not data:
            if not os.path.exists(DATA_FILE):
                return
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

        # build Expense objects from data
        self.expenses = [Expense.from_dict(d) for d in data.get("expenses", [])]
        # reindex sequential ids to ensure consistent numbering
        for idx, exp in enumerate(self.expenses, start=1):
            exp.id = idx
        # set next id to len + 1 (so next add continues sequence)
        self._next_id = int(data.get("next_id", len(self.expenses) + 1))
        # restore categories: ensure DEFAULT_CATEGORIES are present (prepend defaults)
        loaded_cats = data.get("categories", []) or []
        merged = []
        for c in DEFAULT_CATEGORIES:
            if c in loaded_cats:
                merged.append(c)
        # append any other loaded categories preserving their order
        for c in loaded_cats:
            if c not in merged:
                merged.append(c)
        # if no categories were loaded, fall back to defaults
        self.categories = merged or list(DEFAULT_CATEGORIES)

    def balances(self) -> Dict[str, Dict[str, float]]:
        """
        Compute net balances grouped per currency/unit.

        Returns:
            { unit: { participant: balance, ... }, ... }

        Interpretation:
            - For each expense:
                * if shares provided: subtract the share amount from each participant
                * else: compute equal share = amount / len(participants) and subtract it
                * add the full expense amount to the payer in that unit
            - Positive balance => participant should receive money.
            - Negative balance => participant owes money.
        """
        result: Dict[str, Dict[str, float]] = {}
        for e in self.expenses:
            unit = getattr(e, "unit", "EUR") or "EUR"
            bal = result.setdefault(unit, {})
            if e.shares:
                # custom-per-person amounts
                for p, s in e.shares.items():
                    bal.setdefault(p, 0.0)
                    bal[p] -= round(s, 2)
            else:
                participants = e.participants
                if not participants:
                    continue
                share = round(e.amount / len(participants), 2)
                for p in participants:
                    bal.setdefault(p, 0.0)
                    bal[p] -= share
            # payer receives the expense amount
            bal.setdefault(e.payer, 0.0)
            bal[e.payer] += round(e.amount, 2)

        # normalize small rounding noise and round to 2 decimals
        for unit, balances in result.items():
            for p, v in list(balances.items()):
                if abs(v) < 0.005:
                    balances[p] = 0.0
                else:
                    balances[p] = round(v, 2)
        return result

    def settle_suggestions(self) -> Dict[str, List[str]]:
        """
        Produce settle-up suggestions per currency.

        For each currency:
          - Build lists of creditors (positive balances) and debtors (negative balances)
          - Sort descending and greedily match largest creditor with largest debtor
          - Produce strings like "Morgan pays Alessio 12.34 EUR"
        """
        suggestions_by_unit: Dict[str, List[str]] = {}
        balances_by_unit = self.balances()
        for unit, bal in balances_by_unit.items():
            creditors = [(p, amt) for p, amt in bal.items() if amt > 0]
            debtors = [(p, -amt) for p, amt in bal.items() if amt < 0]  # store positive owed for debtors
            creditors.sort(key=lambda x: x[1], reverse=True)
            debtors.sort(key=lambda x: x[1], reverse=True)
            i = j = 0
            suggestions: List[str] = []
            while i < len(debtors) and j < len(creditors):
                d_name, d_amt = debtors[i]
                c_name, c_amt = creditors[j]
                pay = round(min(d_amt, c_amt), 2)
                suggestions.append(f"{d_name} pays {c_name} {pay:.2f} {unit}")
                d_amt -= pay
                c_amt -= pay
                if d_amt <= 0.005:
                    i += 1
                else:
                    debtors[i] = (d_name, d_amt)
                if c_amt <= 0.005:
                    j += 1
                else:
                    creditors[j] = (c_name, c_amt)
            suggestions_by_unit[unit] = suggestions
        return suggestions_by_unit

    def available_periods(self) -> Tuple[List[int], Dict[int, List[int]]]:
        """
        Inspect all expenses and return available years and the months per year.

        Returns:
            (years_list, { year: [month1, month2, ...], ... })
        Useful for populating year/month filters in the UI.
        """
        years = set()
        months_by_year = defaultdict(set)
        for e in self.expenses:
            if not e.date:
                continue
            try:
                d = datetime.date.fromisoformat(e.date)
            except Exception:
                continue
            years.add(d.year)
            months_by_year[d.year].add(d.month)
        years_list = sorted(years)
        months_map = {y: sorted(list(months_by_year[y])) for y in years_list}
        return years_list, months_map

    def totals_by_month(self, year: int, month: int) -> Dict[str, Dict[str, float]]:
        """
        Aggregate totals by currency unit and category for a specific month.
        Output: { unit: { category: total_amount, ... }, ... }
        """
        totals: Dict[str, Dict[str, float]] = {}
        for e in self.list_expenses(year=year, month=month):
            unit = getattr(e, "unit", "EUR") or "EUR"
            m = totals.setdefault(unit, {})
            m[e.category] = round(m.get(e.category, 0.0) + e.amount, 2)
        return totals

    def totals_by_year(self, year: int) -> Dict[str, Dict[str, float]]:
        """
        Aggregate totals by currency unit and category for a whole year.
        """
        totals: Dict[str, Dict[str, float]] = {}
        for e in self.list_expenses(year=year):
            unit = getattr(e, "unit", "EUR") or "EUR"
            m = totals.setdefault(unit, {})
            m[e.category] = round(m.get(e.category, 0.0) + e.amount, 2)
        return totals

    # -----------------------
    # Edit / delete helpers
    # -----------------------
    def edit_expense(self, expense_id: int, **kwargs) -> Optional[Expense]:
        """
        Update an existing expense fields. Supported kwargs:
        amount, payer, participants, category, description, unit, shares, date.
        Returns the updated Expense or None if id not found.
        """
        for e in self.expenses:
            if e.id == expense_id:
                for key in ("amount", "payer", "participants", "category", "description", "unit", "shares", "date"):
                    if key in kwargs:
                        setattr(e, key, kwargs[key])
                # ensure numeric rounding for amount and shares
                try:
                    e.amount = round(float(e.amount), 2)
                except Exception:
                    pass
                if getattr(e, "shares", None):
                    try:
                        e.shares = {p: round(float(v), 2) for p, v in e.shares.items()}
                    except Exception:
                        pass
                self.save()
                return e
        return None

    def delete_expense(self, expense_id: int) -> bool:
        """Remove expense by id. Returns True if deleted, False if not found.

        After removing the expense, reassign sequential ids to remaining expenses
        (1..n) and update self._next_id so new expenses continue numbering.
        """
        try:
            # coerce types to int for reliable comparison
            target_id = int(expense_id)
        except Exception:
            logger.warning("delete_expense called with non-int id: %r", expense_id)
            return False

        logger.info("Attempting to delete expense id=%s", target_id)
        for i, e in enumerate(self.expenses):
            try:
                e_id = int(e.id)
            except Exception:
                # try to handle bad stored id formats
                try:
                    e_id = int(float(e.id))
                except Exception:
                    continue
            if e_id == target_id:
                removed = self.expenses.pop(i)
                # Re-number remaining expenses sequentially starting at 1
                for idx, exp in enumerate(self.expenses, start=1):
                    exp.id = idx
                # Set next id to len + 1
                self._next_id = len(self.expenses) + 1
                try:
                    # persist changes
                    self.save()
                    logger.info("Deleted expense id=%s (category=%s, amount=%s). Reindexed %d expenses.",
                                target_id, getattr(removed, "category", ""), getattr(removed, "amount", ""), len(self.expenses))
                    return True
                except Exception:
                    logger.exception("Error saving after delete")
                    # restore in-memory list if save failed and restore previous ids
                    self.expenses.insert(i, removed)
                    for idx, exp in enumerate(self.expenses, start=1):
                        exp.id = idx
                    self._next_id = len(self.expenses) + 1
                    return False
        logger.info("Expense id=%s not found", target_id)
        return False