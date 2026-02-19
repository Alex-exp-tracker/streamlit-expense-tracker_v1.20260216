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
import datetime


DEFAULT_TIMELINE_START = datetime.date(2025, 1, 1)


def _month_start_from_expense(expense):
    try:
        d = datetime.date.fromisoformat(getattr(expense, "date", ""))
    except Exception:
        return None
    return datetime.date(d.year, d.month, 1)


def _default_month_range(month_values):
    present_month = datetime.date.today().replace(day=1)
    end_candidates = [m for m in month_values if m <= present_month]
    default_end = end_candidates[-1] if end_candidates else month_values[-1]

    start_candidates = [m for m in month_values if DEFAULT_TIMELINE_START <= m <= default_end]
    default_start = start_candidates[0] if start_candidates else month_values[0]
    return default_start, default_end


def _select_month_range(month_values, start_key: str, end_key: str):
    month_labels = [m.strftime("%Y-%m") for m in month_values]
    default_start, default_end = _default_month_range(month_values)
    start_label = st.selectbox(
        "Start month",
        options=month_labels,
        index=month_values.index(default_start),
        key=start_key,
    )
    end_label = st.selectbox(
        "End month",
        options=month_labels,
        index=month_values.index(default_end),
        key=end_key,
    )
    start_month = month_values[month_labels.index(start_label)]
    end_month = month_values[month_labels.index(end_label)]
    if start_month > end_month:
        start_month, end_month = end_month, start_month
    return start_month, end_month


def _filter_expenses_by_month_range(expenses, start_month: datetime.date, end_month: datetime.date):
    filtered = []
    for e in expenses:
        month_start = _month_start_from_expense(e)
        if month_start is None:
            continue
        if start_month <= month_start <= end_month:
            filtered.append(e)
    return filtered


def main():
    """
    Streamlit page: sidebar menu controls which view is shown.
    Actions:
      - Add Expense: show form and persist via tracker.add_expense
      - List Expenses: optional year/month filters
      - Show Balances: per-year balances by currency, with CHF settle suggestions
      - Category Totals: aggregate by selected month or year
      - Clear All Expenses: reset data (with single-button confirmation)
    """
    st.title("Expense Tracker Dashboard")
    tracker = ExpenseTracker()
    fx_snapshot = tracker.get_fx_snapshot()
    fx_rates = fx_snapshot.get("rates", {"CHF": 1.0})
    backend_name, backend_msg = tracker.storage_status()
    if backend_name == "google_sheets":
        st.sidebar.success(backend_msg)
    else:
        st.sidebar.warning(backend_msg)
        st.sidebar.caption(
            "For indefinite cloud persistence, set GOOGLE_SHEET_ID and "
            "GOOGLE_SERVICE_ACCOUNT_JSON in Streamlit app Secrets."
        )
    if fx_snapshot.get("error"):
        st.sidebar.warning(fx_snapshot.get("error"))
    else:
        as_of = fx_snapshot.get("as_of", "")
        source = fx_snapshot.get("source", "FX provider")
        if as_of:
            st.sidebar.caption(f"{source} rates as of {as_of}")
        else:
            st.sidebar.caption(f"{source} rates loaded")
    if fx_snapshot.get("stale"):
        st.sidebar.caption("FX rates may be stale.")

    menu = [
        "Add Expense",
        "List Expenses",
        "Show Balances",
        "Category Totals",
        "Expenses over time",
        "Categories over time",
        "Edit Expense",
        "Clear All Expenses",
    ]
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
        grand_total_chf, skipped_units = tracker.grand_total_chf(expenses=expenses, rates=fx_rates)
        components.display_expense_list(
            expenses,
            grand_total_chf=grand_total_chf,
            fx_snapshot=fx_snapshot,
            skipped_units=skipped_units,
        )

    elif choice == "Show Balances":
        years, _ = tracker.available_periods()
        yearly_balances = {}
        for year in years:
            year_expenses = tracker.list_expenses(year=year)
            yearly_balances[year] = tracker.balances_for_expenses(year_expenses)

        overall_suggestions = tracker.settle_suggestions_chf(rates=fx_rates)
        overall_settle_sentence_chf = overall_suggestions[0] if overall_suggestions else None
        components.display_balances(
            yearly_balances,
            overall_settle_sentence_chf=overall_settle_sentence_chf,
        )

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
                    totals_chf, skipped_units = tracker.totals_by_month_chf(year_sel, month_sel, rates=fx_rates)
                    components.display_category_totals_chf(
                        totals_chf,
                        grand_total_chf=round(sum(totals_chf.values()), 2),
                        fx_snapshot=fx_snapshot,
                        skipped_units=skipped_units,
                    )
            else:
                totals_chf, skipped_units = tracker.totals_by_year_chf(year_sel, rates=fx_rates)
                components.display_category_totals_chf(
                    totals_chf,
                    grand_total_chf=round(sum(totals_chf.values()), 2),
                    fx_snapshot=fx_snapshot,
                    skipped_units=skipped_units,
                )

    elif choice == "Expenses over time":
        exs = tracker.list_expenses()
        grand_total_chf, all_skipped_units = tracker.grand_total_chf(expenses=exs, rates=fx_rates)
        filtered_exs = exs
        total_for_period_chf = None
        skipped_units_for_view = all_skipped_units

        month_values = sorted(
            {
                month_start
                for month_start in (_month_start_from_expense(e) for e in exs)
                if month_start is not None
            }
        )

        if month_values:
            start_month, end_month = _select_month_range(
                month_values,
                start_key="expenses_over_time_start_month",
                end_key="expenses_over_time_end_month",
            )
            filtered_exs = _filter_expenses_by_month_range(exs, start_month, end_month)
            total_for_period_chf, skipped_units_for_view = tracker.grand_total_chf(
                expenses=filtered_exs,
                rates=fx_rates,
            )

        components.display_expenses_over_time(
            filtered_exs,
            chf_rates=fx_rates,
            grand_total_chf=grand_total_chf,
            total_for_period_chf=total_for_period_chf,
            fx_snapshot=fx_snapshot,
            skipped_units=skipped_units_for_view,
        )

    elif choice == "Categories over time":
        exs = tracker.list_expenses()
        categories = sorted(
            {
                str(getattr(e, "category", "")).strip()
                for e in exs
                if str(getattr(e, "category", "")).strip()
            }
        )
        if not categories:
            st.info("No expenses recorded yet.")
        else:
            selected_category = st.selectbox("Category", options=categories)
            category_exs = [
                e for e in exs if str(getattr(e, "category", "")).strip() == selected_category
            ]
            grand_total_chf, all_skipped_units = tracker.grand_total_chf(
                expenses=category_exs,
                rates=fx_rates,
            )
            filtered_exs = category_exs
            total_for_period_chf = None
            skipped_units_for_view = all_skipped_units

            month_values = sorted(
                {
                    month_start
                    for month_start in (_month_start_from_expense(e) for e in category_exs)
                    if month_start is not None
                }
            )
            if month_values:
                start_month, end_month = _select_month_range(
                    month_values,
                    start_key="categories_over_time_start_month",
                    end_key="categories_over_time_end_month",
                )
                filtered_exs = _filter_expenses_by_month_range(category_exs, start_month, end_month)
                total_for_period_chf, skipped_units_for_view = tracker.grand_total_chf(
                    expenses=filtered_exs,
                    rates=fx_rates,
                )

            components.display_expenses_over_time(
                filtered_exs,
                chf_rates=fx_rates,
                grand_total_chf=grand_total_chf,
                total_for_period_chf=total_for_period_chf,
                fx_snapshot=fx_snapshot,
                skipped_units=skipped_units_for_view,
                chart_title=f"Category over time: {selected_category}",
            )

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
