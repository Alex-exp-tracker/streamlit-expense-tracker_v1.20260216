"""
tracker.py - core application logic and persistence

Responsibilities:
 - keep an in-memory list of Expense objects
 - persist/load data to Google Sheets (preferred) or local JSON fallback
 - provide helper APIs consumed by the UI:
     add_expense, list_expenses(filter by year/month),
     balances (grouped by currency), settle_suggestions (per currency),
     totals_by_month/year, category management
"""

from typing import List, Dict, Optional, Tuple, Any
import sys
from src.models import Expense
import json
import os
import datetime
from collections import defaultdict
import ast
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


class GoogleSheetsBackend:
    """
    Google Sheets persistence backend.

    Data layout:
      - worksheet "expenses": tabular rows of expenses
      - worksheet "meta": key/value metadata (next_id, categories)
    """

    EXPENSES_SHEET_NAME = "expenses"
    META_SHEET_NAME = "meta"
    EXPENSE_HEADERS = [
        "id",
        "amount",
        "payer",
        "participants",
        "category",
        "description",
        "unit",
        "shares_json",
        "date",
    ]
    META_HEADERS = ["key", "value"]
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self):
        self.available = False
        self.reason = ""
        self.sheet_id = (os.getenv("GOOGLE_SHEET_ID") or "").strip()
        self._spreadsheet = None
        self._expenses_ws = None
        self._meta_ws = None

        if not self.sheet_id:
            self.reason = "GOOGLE_SHEET_ID is not set"
            return
        if gspread is None or Credentials is None:
            self.reason = "Google Sheets dependencies are unavailable"
            return

        try:
            creds = self._build_credentials()
            client = gspread.authorize(creds)
            self._spreadsheet = client.open_by_key(self.sheet_id)
            self._expenses_ws = self._get_or_create_worksheet(
                self.EXPENSES_SHEET_NAME, rows=1000, cols=max(12, len(self.EXPENSE_HEADERS))
            )
            self._meta_ws = self._get_or_create_worksheet(self.META_SHEET_NAME, rows=200, cols=4)
            self._ensure_headers()
            self.available = True
        except Exception as exc:
            self.available = False
            self.reason = f"Google Sheets init failed ({exc.__class__.__name__})"
            logger.warning("Google Sheets backend unavailable: %s", self.reason)

    def _build_credentials(self):
        service_account_json = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
        service_account_file = (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()

        if service_account_json:
            try:
                info = json.loads(service_account_json)
            except Exception:
                # tolerate Python-dict style strings often used by mistake in env vars
                info = ast.literal_eval(service_account_json)
            if not isinstance(info, dict):
                raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON must decode to an object")
            return Credentials.from_service_account_info(info, scopes=self.SCOPES)

        if service_account_file:
            return Credentials.from_service_account_file(service_account_file, scopes=self.SCOPES)

        # Fallback to application default credentials if available.
        import google.auth
        creds, _ = google.auth.default(scopes=self.SCOPES)
        return creds

    def _get_or_create_worksheet(self, title: str, rows: int, cols: int):
        try:
            return self._spreadsheet.worksheet(title)
        except Exception:
            return self._spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

    @staticmethod
    def _ensure_sheet_size(ws, min_rows: int, min_cols: int):
        new_rows = max(ws.row_count, min_rows)
        new_cols = max(ws.col_count, min_cols)
        if new_rows != ws.row_count or new_cols != ws.col_count:
            ws.resize(rows=new_rows, cols=new_cols)

    def _ensure_headers(self):
        # Keep headers explicit so sheet is always readable and Excel-like.
        if self._expenses_ws:
            first = self._expenses_ws.row_values(1) or []
            if [x.strip() for x in first] != self.EXPENSE_HEADERS:
                self._ensure_sheet_size(self._expenses_ws, 2, len(self.EXPENSE_HEADERS))
                self._expenses_ws.update(
                    range_name="A1",
                    values=[self.EXPENSE_HEADERS],
                    value_input_option="RAW",
                )
        if self._meta_ws:
            first = self._meta_ws.row_values(1) or []
            if [x.strip() for x in first] != self.META_HEADERS:
                self._ensure_sheet_size(self._meta_ws, 2, len(self.META_HEADERS))
                self._meta_ws.update(
                    range_name="A1",
                    values=[self.META_HEADERS],
                    value_input_option="RAW",
                )

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(str(value).strip()))
        except Exception:
            return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(str(value).strip())
        except Exception:
            return default

    @staticmethod
    def _parse_json_or_literal(value: Any):
        if isinstance(value, (list, dict)):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(text)
            except Exception:
                continue
        return None

    @classmethod
    def _parse_participants(cls, value: Any) -> List[str]:
        parsed = cls._parse_json_or_literal(value)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        text = str(value or "").strip()
        if not text:
            return []
        return [p.strip() for p in text.split(",") if p.strip()]

    @classmethod
    def _parse_shares(cls, value: Any) -> Dict[str, float]:
        parsed = cls._parse_json_or_literal(value)
        if not isinstance(parsed, dict):
            return {}
        out: Dict[str, float] = {}
        for k, v in parsed.items():
            name = str(k).strip()
            if not name:
                continue
            amt = cls._to_float(v, 0.0)
            out[name] = round(amt, 2)
        return out

    @classmethod
    def _parse_categories(cls, value: Any) -> List[str]:
        parsed = cls._parse_json_or_literal(value)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        text = str(value or "").strip()
        if not text:
            return []
        return [p.strip() for p in text.split(",") if p.strip()]

    @classmethod
    def _record_to_expense_dict(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        shares_raw = record.get("shares_json", record.get("shares", ""))
        unit = str(record.get("unit", "")).strip() or "EUR"
        return {
            "id": cls._to_int(record.get("id", 0), 0),
            "amount": round(cls._to_float(record.get("amount", 0.0), 0.0), 2),
            "payer": str(record.get("payer", "")).strip(),
            "participants": cls._parse_participants(record.get("participants", "")),
            "category": str(record.get("category", "")).strip(),
            "description": str(record.get("description", "")).strip(),
            "unit": unit,
            "shares": cls._parse_shares(shares_raw),
            "date": str(record.get("date", "")).strip(),
        }

    def save_state(self, data: Dict[str, Any]) -> bool:
        if not self.available:
            return False

        try:
            self._ensure_headers()
            expenses = list(data.get("expenses", []) or [])
            categories = list(data.get("categories", []) or [])
            next_id = max(1, self._to_int(data.get("next_id", len(expenses) + 1), len(expenses) + 1))

            expense_rows = [self.EXPENSE_HEADERS]
            for e in expenses:
                participants_json = json.dumps(e.get("participants", []) or [], ensure_ascii=False)
                shares_json = json.dumps(e.get("shares", {}) or {}, ensure_ascii=False)
                expense_rows.append(
                    [
                        str(self._to_int(e.get("id", 0), 0)),
                        f"{round(self._to_float(e.get('amount', 0.0), 0.0), 2):.2f}",
                        str(e.get("payer", "") or ""),
                        participants_json,
                        str(e.get("category", "") or ""),
                        str(e.get("description", "") or ""),
                        str(e.get("unit", "EUR") or "EUR"),
                        shares_json,
                        str(e.get("date", "") or ""),
                    ]
                )

            meta_rows = [
                self.META_HEADERS,
                ["next_id", str(next_id)],
                ["categories", json.dumps(categories, ensure_ascii=False)],
            ]

            self._ensure_sheet_size(self._expenses_ws, len(expense_rows) + 10, len(self.EXPENSE_HEADERS))
            self._ensure_sheet_size(self._meta_ws, len(meta_rows) + 5, len(self.META_HEADERS))

            # Use RAW to store user content as plain values (not spreadsheet formulas).
            self._expenses_ws.clear()
            self._expenses_ws.update(
                range_name="A1",
                values=expense_rows,
                value_input_option="RAW",
            )

            self._meta_ws.clear()
            self._meta_ws.update(
                range_name="A1",
                values=meta_rows,
                value_input_option="RAW",
            )
            return True
        except Exception:
            logger.exception("Failed to save tracker state to Google Sheets")
            return False

    def load_state(self) -> Dict[str, Any]:
        if not self.available:
            return {}

        try:
            self._ensure_headers()

            expenses: List[Dict[str, Any]] = []
            exp_values = self._expenses_ws.get_all_values() or []
            if exp_values:
                raw_headers = exp_values[0]
                headers = [str(h).strip().lower() for h in raw_headers]
                for row in exp_values[1:]:
                    if not any(str(c).strip() for c in row):
                        continue
                    record: Dict[str, Any] = {}
                    for idx, header in enumerate(headers):
                        if not header:
                            continue
                        record[header] = row[idx] if idx < len(row) else ""
                    if not any(record.values()):
                        continue
                    expenses.append(self._record_to_expense_dict(record))

            meta_map: Dict[str, str] = {}
            meta_values = self._meta_ws.get_all_values() or []
            for row in meta_values[1:]:
                if not row:
                    continue
                key = str(row[0]).strip() if len(row) > 0 else ""
                value = str(row[1]).strip() if len(row) > 1 else ""
                if key:
                    meta_map[key] = value

            next_id = self._to_int(meta_map.get("next_id", ""), len(expenses) + 1)
            categories = self._parse_categories(meta_map.get("categories", ""))
            return {
                "next_id": max(1, next_id),
                "expenses": expenses,
                "categories": categories,
            }
        except Exception:
            logger.exception("Failed to load tracker state from Google Sheets")
            return {}


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
        # initialize Google Sheets backend if configured
        self._gs_backend = GoogleSheetsBackend()
        # When running tests, ensure we start from a clean state by removing
        # any temp data file left from previous test runs.
        if any("pytest" in p for p in sys.argv) or os.getenv("PYTEST_CURRENT_TEST"):
            try:
                if os.path.exists(DATA_FILE):
                    os.remove(DATA_FILE)
            except Exception:
                pass
        self.load()

    def uses_google_sheets(self) -> bool:
        """True when the durable Google Sheets backend is active."""
        return bool(getattr(self, "_gs_backend", None) and self._gs_backend.available)

    def storage_status(self) -> Tuple[str, str]:
        """
        Return current storage backend and a short diagnostic message for the UI.
        """
        if self.uses_google_sheets():
            return "google_sheets", "Persistent storage active (Google Sheets)."
        reason = getattr(self._gs_backend, "reason", "Google Sheets not configured")
        return "local_json", f"Using local file fallback: {reason}."

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
        # Refresh from remote before mutating to reduce stale-session overwrites.
        if self.uses_google_sheets():
            self.load()
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
        if self.uses_google_sheets():
            self.load()
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
        Load tracker state from Google Sheets when configured, otherwise local JSON.
        If no saved data exists, defaults remain (empty expenses, default categories).
        IDs are kept stable; _next_id is set to at least max(existing_id) + 1.
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

        # build Expense objects from data and normalize malformed ids.
        self.expenses = [Expense.from_dict(d) for d in data.get("expenses", [])]
        max_id = 0
        for exp in self.expenses:
            try:
                exp_id = int(exp.id)
            except Exception:
                exp_id = 0
            if exp_id <= 0:
                max_id += 1
                exp.id = max_id
            else:
                max_id = max(max_id, exp_id)

        # Keep ids stable across reloads/deletes; next id should always exceed max existing id.
        try:
            next_id_raw = int(data.get("next_id", max_id + 1))
        except Exception:
            next_id_raw = max_id + 1
        self._next_id = max(next_id_raw, max_id + 1)
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
        if self.uses_google_sheets():
            self.load()
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

        IDs are not renumbered, to keep references stable across sessions.
        """
        if self.uses_google_sheets():
            self.load()
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
                # Keep next id monotonic to avoid id reuse.
                max_existing = 0
                for exp in self.expenses:
                    try:
                        max_existing = max(max_existing, int(exp.id))
                    except Exception:
                        continue
                self._next_id = max(self._next_id, max_existing + 1)
                try:
                    # persist changes
                    self.save()
                    logger.info("Deleted expense id=%s (category=%s, amount=%s). Remaining expenses=%d.",
                                target_id, getattr(removed, "category", ""), getattr(removed, "amount", ""), len(self.expenses))
                    return True
                except Exception:
                    logger.exception("Error saving after delete")
                    # restore in-memory list if save failed
                    self.expenses.insert(i, removed)
                    max_existing = 0
                    for exp in self.expenses:
                        try:
                            max_existing = max(max_existing, int(exp.id))
                        except Exception:
                            continue
                    self._next_id = max(self._next_id, max_existing + 1)
                    return False
        logger.info("Expense id=%s not found", target_id)
        return False
