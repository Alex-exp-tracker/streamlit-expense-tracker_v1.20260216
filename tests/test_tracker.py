import pytest
from src.tracker import ExpenseTracker
from src.models import Expense

def test_add_expense():
    tracker = ExpenseTracker()
    initial_count = len(tracker.expenses)
    tracker.add_expense(100.0, "Alice", ["Alice", "Bob"], "Food", "Dinner")
    assert len(tracker.expenses) == initial_count + 1
    assert tracker.expenses[-1].amount == 100.0
    assert tracker.expenses[-1].payer == "Alice"
    assert "Alice" in tracker.expenses[-1].participants
    assert "Bob" in tracker.expenses[-1].participants

def test_balances():
    tracker = ExpenseTracker()
    tracker.add_expense(100.0, "Alice", ["Alice", "Bob"], "Food", "Dinner")
    tracker.add_expense(50.0, "Bob", ["Alice", "Bob"], "Transport", "Taxi")
    balances = tracker.balances()
    assert balances["Alice"] == 25.0
    assert balances["Bob"] == -25.0

def test_settle_suggestions():
    tracker = ExpenseTracker()
    tracker.add_expense(100.0, "Alice", ["Alice", "Bob"], "Food", "Dinner")
    tracker.add_expense(50.0, "Bob", ["Alice", "Bob"], "Transport", "Taxi")
    suggestions = tracker.settle_suggestions()
    assert len(suggestions) == 1
    assert "Alice pays Bob 25.00" in suggestions

def test_clear_expenses():
    tracker = ExpenseTracker()
    tracker.add_expense(100.0, "Alice", ["Alice", "Bob"], "Food", "Dinner")
    tracker.clear()
    assert len(tracker.expenses) == 0
    assert tracker._next_id == 1

def test_load_expenses():
    tracker = ExpenseTracker()
    tracker.add_expense(100.0, "Alice", ["Alice", "Bob"], "Food", "Dinner")
    tracker.save()
    new_tracker = ExpenseTracker()
    assert len(new_tracker.expenses) == 1
    assert new_tracker.expenses[0].amount == 100.0
    assert new_tracker.expenses[0].payer == "Alice"