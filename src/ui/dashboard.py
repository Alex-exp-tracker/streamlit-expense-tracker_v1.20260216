"""
dashboard.py - Streamlit UI entrypoint and orchestration

This module wires the UI components (src.ui.components) with the business logic
(src.tracker). The main() function builds the sidebar menu and routes actions
to components and tracker methods.

Design notes:
 - Keep the dashboard responsible only for UI orchestration and presentation.
 - All persistence and business rules live in src.tracker.
 - Components return lightweight data objects (ExpenseInput) to keep wiring simple.
"""

import streamlit as st
from src.tracker import ExpenseTracker
from src.ui import components
from typing import Dict, List
import datetime


def display_expenses(expenses):
    """
    Small wrapper used by the dashboard when viewing the List Expenses screen.
    Delegates rendering to components.display_expense_list for consistent formatting.
    """
    components.display_expense_list(expenses)


def display_balances(balances_by_unit: Dict[str, Dict[str, float]]):
    """Wrapper to render balances using the components helper."""
    components.display_balances(balances_by_unit)


def display_settle_suggestions(suggestions_by_unit: Dict[str, List[str]]):
    """Wrapper to render settle suggestions using the components helper."""
    components.display_settle_suggestions(suggestions_by_unit)


def display_category_totals(totals_by_unit: Dict[str, Dict[str, float]]):
    """Wrapper to render category totals using the components helper."""
    components.display_category_totals(totals_by_unit)


def main():
    """
    Streamlit page: sidebar menu controls which view is shown.
    Actions:
      - Add Expense: show form and persist via tracker.add_expense
      - List Expenses: optional year/month filters
      - Show Balances: grouped by currency unit
      - Show Settle Suggestions: per currency
      - Category Totals: aggregate by selected month or year
      - Clear All Expenses: reset data (with single-button confirmation)
    """
    st.title("Expense Tracker Dashboard")
    tracker = ExpenseTracker()
    backend_name, backend_msg = tracker.storage_status()
    if backend_name == "google_sheets":
        st.sidebar.success(backend_msg)
    else:
        st.sidebar.warning(backend_msg)
        st.sidebar.caption(
            "For indefinite cloud persistence, set GOOGLE_SHEET_ID and "
            "GOOGLE_SERVICE_ACCOUNT_JSON in Streamlit app Secrets."
        )

    menu = ["Add Expense", "List Expenses", "Show Balances", "Show Settle Suggestions", "Category Totals", "Expenses over time", "Edit Expense", "Clear All Expenses"]
    choice = st.sidebar.selectbox("Select an option", menu)

    if choice == "Add Expense":
        # prepare a callback to receive the ExpenseInput produced by the form
        def on_submit(exp_input: components.ExpenseInput):
            tracker.add_expense(
                amount=exp_input.amount,
                payer=exp_input.payer,
                participants=exp_input.participants,
                category=exp_input.category,
                description=getattr(exp_input, "description", ""),
                unit=exp_input.unit,
                shares=getattr(exp_input, "shares", {}) or {},
                date=getattr(exp_input, "date", ""),
            )

        # pass categories and add_category function so the form can persist new categories
        components.display_expense_form(on_submit, tracker.get_categories(), tracker.add_category)

    elif choice == "List Expenses":
        # show year/month filters derived from available expense dates
        years, months_map = tracker.available_periods()
        col1, col2 = st.columns(2)
        with col1:
            # Provide None as first option (no filtering)
            year_sel = st.selectbox("Filter year (optional)", options=[None] + years, index=0)
        with col2:
            month_options = months_map.get(year_sel, []) if year_sel else []
            month_sel = st.selectbox("Filter month (optional)", options=[None] + month_options, index=0)
        # retrieve filtered list and display
        if year_sel is None:
            expenses = tracker.list_expenses()
        else:
            expenses = tracker.list_expenses(year=year_sel, month=month_sel)
        display_expenses(expenses)

    elif choice == "Show Balances":
        balances_by_unit = tracker.balances()
        display_balances(balances_by_unit)

    elif choice == "Show Settle Suggestions":
        suggestions_by_unit = tracker.settle_suggestions()
        display_settle_suggestions(suggestions_by_unit)

    elif choice == "Category Totals":
        # allow the user to aggregate totals for a month or a whole year
        mode = st.radio("Aggregate by", options=["Month", "Year"])
        years, months_map = tracker.available_periods()
        if not years:
            st.info("No expenses recorded yet.")
        else:
            # default to the most recent year that exists in data
            year_sel = st.selectbox("Year", options=years, index=len(years) - 1)
            if mode == "Month":
                month_options = months_map.get(year_sel, [])
                if not month_options:
                    st.info("No months for selected year.")
                else:
                    month_sel = st.selectbox("Month", options=month_options, index=0)
                    totals = tracker.totals_by_month(year_sel, month_sel)
                    display_category_totals(totals)
            else:
                totals = tracker.totals_by_year(year_sel)
                display_category_totals(totals)

    elif choice == "Expenses over time":
        exs = tracker.list_expenses()
        components.display_expenses_over_time(exs)

    elif choice == "Edit Expense":
        # New: show edit/delete UI
        components.display_manage_expenses(tracker)

    elif choice == "Clear All Expenses":
        # simple confirm button to avoid accidental data loss
        if st.button("Confirm Clear"):
            tracker.clear()
            st.success("All expenses cleared.")


if __name__ == "__main__":
    main()
