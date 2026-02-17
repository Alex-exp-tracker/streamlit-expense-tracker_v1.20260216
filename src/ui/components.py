"""
components.py - reusable Streamlit components / forms / displays

This module contains pure-UI helpers used by the dashboard:
 - display_expense_form(on_submit, categories, add_category_cb)
 - display_expense_list / balances / settle suggestions / category totals

The form enforces validation rules:
 - amount > 0
 - payer must be one of the allowed household names (Alessio, Morgan)
 - at least one participant selected
 - date mandatory (st.date_input)
 - if custom shares selected, per-person shares must be >0 and sum == amount (tolerance)
 - new categories can be added and are persisted through add_category_cb
"""

from dataclasses import dataclass
from typing import Dict, List, Callable, Optional, Any
from src.models import Expense
from src.tracker import DEFAULT_CATEGORIES
import datetime
import streamlit as st
import pandas as pd
from io import BytesIO
import json
import time
import altair as alt

# Trigger a Streamlit rerun in a way compatible with multiple Streamlit versions.
def _trigger_rerun():
    # Prefer direct experimental rerun if available
    try:
        if hasattr(st, "experimental_rerun"):
            st.experimental_rerun()
            return
    except Exception:
        pass
    # Fallback: mutate query params to force a rerun (use st.query_params API)
    try:
        params = dict(st.query_params or {})
        params["_rerun"] = [str(int(time.time()))]
        st.query_params = params
        return
    except Exception:
        pass
    # Last resort: toggle a session_state key
    try:
        st.session_state["_rerun_flag"] = st.session_state.get("_rerun_flag", 0) + 1
    except Exception:
        pass


@dataclass
class ExpenseInput:
    """Lightweight container passed to the on_submit callback."""
    amount: float
    payer: str
    participants: list
    category: str
    description: str
    unit: str
    shares: Dict[str, float]
    date: str  # ISO date string


def _show_grand_total_chf(
    grand_total_chf: Optional[float],
    fx_snapshot: Optional[Dict[str, Any]] = None,
):
    if grand_total_chf is None:
        return
    st.markdown(f"**Grand Total: {grand_total_chf:.2f} CHF**")
    if fx_snapshot:
        source = str(fx_snapshot.get("source", "FX provider"))
        as_of = str(fx_snapshot.get("as_of", "") or "")
        stale = bool(fx_snapshot.get("stale", False))
        if as_of:
            st.caption(f"Converted using {source} rates as of {as_of}" + (" (stale cache)" if stale else ""))
        else:
            st.caption(f"Converted using {source}" + (" (stale cache)" if stale else ""))


def _show_skipped_units(skipped_units: Optional[Dict[str, float]] = None):
    if not skipped_units:
        return
    parts = [f"{unit}: {amount:.2f}" for unit, amount in sorted(skipped_units.items())]
    st.caption("Excluded from CHF conversion (missing rate): " + ", ".join(parts))


def display_expense_form(on_submit: Callable[[ExpenseInput], None],
                         categories: List[str],
                         add_category_cb: Callable[[str], bool]):
    """
    Display the 'Add Expense' form.

    Parameters:
      - on_submit: callback invoked with ExpenseInput when the form validates
      - categories: list of categories to show in the dropdown
      - add_category_cb: function(name)->bool used to persist a new category
    """
    st.header("Add Expense")
    # Use a Streamlit form so the whole payload is submitted at once
    with st.form(key="expense_form"):
        # Basic fields
        amount = st.number_input("Amount", min_value=0.0, format="%.2f")
        unit = st.selectbox("Unit / Currency", options=["EUR", "USD", "GBP", "CHF", "other"])
        # Payer restricted to the two household members (UI enforces allowed values)
        payer = st.selectbox("Payer Name", options=["Alessio", "Morgan"])
        # Participants chosen from the same two names; default both selected
        participants = st.multiselect("Participants", options=["Alessio", "Morgan"], default=["Alessio", "Morgan"])

        # Category selection with an "Add new category..." entry.
        extra_opt = "Add new category..."
        cat_options = list(categories) + [extra_opt]
        selected_cat = st.selectbox("Category", options=cat_options)
        new_category_name = ""
        if selected_cat == extra_opt:
            # When user wants to add a new category, provide a text input and Add button.
            # When user wants to add a new category, provide a text input.
            # The new category will be created when the whole form is submitted.
            new_category_name = st.text_input("New category name")
            if not new_category_name:
                st.info("Type a category name and submit the form to add it.")

        # Date input: required for filtering (stored as ISO string)
        date_val = st.date_input("Date", value=datetime.date.today())
        # Optional free-text note for informational context in the expense list.
        description = st.text_input("Expense description (optional)")

        # Split mode: equal or custom shares
        split_mode = st.radio("Split mode", options=["Equal split", "Custom shares"])
        custom_shares: Dict[str, float] = {}
        if split_mode == "Custom shares":
            st.write("Enter custom share amounts (must sum to total amount):")
            # Show one input per selected participant
            for p in participants:
                # use unique key so Streamlit can differentiate inputs
                custom_shares[p] = st.number_input(f"Share for {p}", min_value=0.0, format="%.2f", key=f"share_{p}")

        submit_button = st.form_submit_button("Add Expense")

        if submit_button:
            # Basic validation logic before constructing ExpenseInput
            participants_list = [p for p in participants]
            if amount <= 0:
                st.error("Amount must be greater than 0.")
                return
            if not payer.strip():
                st.error("Payer name is required.")
                return
            if not participants_list:
                st.error("At least one participant is required.")
                return

            # Convert the date value to ISO string ("YYYY-MM-DD")
            try:
                date_iso = date_val.isoformat()
            except Exception:
                st.error("Invalid date.")
                return

            # Determine final category: if the user chose "Add new category..." and
            # didn't press Add, allow adding it now (and persist).
            category_final = selected_cat
            if selected_cat == extra_opt:
                if new_category_name.strip():
                    added = add_category_cb(new_category_name.strip())
                    if added:
                        category_final = new_category_name.strip()
                        st.success(f"Category '{category_final}' added and selected.")
                        # Rerun to show the newly added category in the selectbox
                        _trigger_rerun()
                    else:
                        st.error("Could not add category (it may already exist).")
                        return
                else:
                    st.error("Please either pick an existing category or type a new one and submit the form.")
                    return

            # Validate custom shares when selected
            shares: Dict[str, float] = {}
            if split_mode == "Custom shares":
                total_shares = sum(custom_shares.get(p, 0.0) for p in participants_list)
                total_shares = round(total_shares, 2)
                if any(custom_shares.get(p, 0.0) <= 0 for p in participants_list):
                    st.error("All custom shares must be greater than 0.")
                    return
                if abs(total_shares - round(amount, 2)) > 0.01:
                    st.error(f"Custom shares sum to {total_shares:.2f} but amount is {amount:.2f}. Adjust shares.")
                    return
                shares = {p: round(custom_shares[p], 2) for p in participants_list}

            # Build ExpenseInput and hand back to caller
            expense = ExpenseInput(
                amount=round(amount, 2),
                payer=payer,
                participants=participants_list,
                category=category_final,
                description=description.strip(),
                unit=unit,
                shares=shares,
                date=date_iso,
            )
            on_submit(expense)
            st.success("Expense added.")


def display_expense_list(
    expenses: List[Expense],
    grand_total_chf: Optional[float] = None,
    fx_snapshot: Optional[Dict[str, Any]] = None,
    skipped_units: Optional[Dict[str, float]] = None,
):
    """
    Render expenses as an interactive table and provide an XLSX export button.

    Input:
      - expenses: list of Expense objects (from tracker.list_expenses())

    The exported spreadsheet contains columns:
      id, date, category, amount, unit, payer, participants, description, shares_json
    """
    st.header("Expense List (table)")
    if not expenses:
        st.write("No expenses recorded.")
        return
    _show_grand_total_chf(grand_total_chf, fx_snapshot=fx_snapshot)
    _show_skipped_units(skipped_units)

    # Build DataFrame from expenses
    rows = []
    for e in expenses:
        rows.append({
            "id": int(getattr(e, "id", "")),
            "date": getattr(e, "date", ""),
            "category": getattr(e, "category", ""),
            "amount": float(getattr(e, "amount", 0.0)),
            "unit": getattr(e, "unit", "EUR"),
            "payer": getattr(e, "payer", ""),

            # participants stored as list -> join into string for display/export
            "participants": ", ".join(getattr(e, "participants", []) or []),
            "description": getattr(e, "description", ""),

            # keep shares as JSON string to preserve mapping
            "shares_json": json.dumps(getattr(e, "shares", {}) or {}),
        })

    df = pd.DataFrame(rows, columns=["id", "date", "category", "amount", "unit", "payer", "participants", "description", "shares_json"])

    # Display as interactive table
    st.dataframe(df.style.format({"amount": "{:.2f}"}), use_container_width=True)

    # Add a simple summary (totals per currency)
    totals = df.groupby("unit")["amount"].sum().reset_index()
    st.markdown("**Totals by currency**")
    for _, r in totals.iterrows():
        st.write(f"- {r['unit']}: {r['amount']:.2f}")

    # Provide XLSX export
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="expenses")
        # write totals to a separate sheet
        totals.to_excel(writer, index=False, sheet_name="totals_by_currency")
    # context manager already saved into buffer
    buffer.seek(0)
    bts = buffer.getvalue()

    st.download_button(
        label="Download as XLSX",
        data=bts,
        file_name="expenses.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def display_balances(
    balances_by_unit: Dict[str, Dict[str, float]],
    grand_total_chf: Optional[float] = None,
    fx_snapshot: Optional[Dict[str, Any]] = None,
    skipped_units: Optional[Dict[str, float]] = None,
):
    """Show balances grouped per currency unit."""
    st.header("Balances")
    _show_grand_total_chf(grand_total_chf, fx_snapshot=fx_snapshot)
    _show_skipped_units(skipped_units)
    if balances_by_unit:
        for unit, balances in balances_by_unit.items():
            st.markdown(f"**{unit}**")
            if not balances:
                st.write("  No entries.")
                continue
            for participant, balance in balances.items():
                st.write(f"  {participant}: {balance:.2f} {unit}")
    else:
        st.write("No balances to display.")

def display_expenses_over_time(
    expenses: List[Expense],
    chf_rates: Optional[Dict[str, float]] = None,
    grand_total_chf: Optional[float] = None,
    fx_snapshot: Optional[Dict[str, Any]] = None,
    skipped_units: Optional[Dict[str, float]] = None,
):
    """Show stacked monthly bars of expenses over time.

    When chf_rates is provided, amounts are converted and aggregated in CHF only.
    """
    st.header("Expenses over time")
    if not expenses:
        st.write("No expenses recorded.")
        return
    _show_grand_total_chf(grand_total_chf, fx_snapshot=fx_snapshot)
    _show_skipped_units(skipped_units)

    # Build DataFrame with a month-start datetime column.
    rows = []
    for e in expenses:
        try:
            dt = pd.to_datetime(getattr(e, "date", ""), errors="coerce")
        except Exception:
            dt = pd.NaT
        unit = str(getattr(e, "unit", "EUR") or "EUR").upper()
        amount = float(getattr(e, "amount", 0.0))
        if chf_rates is not None:
            rate = chf_rates.get(unit)
            if rate is None:
                continue
            amount = amount * float(rate)
            unit = "CHF"

        rows.append({
            "date": dt,
            "month": pd.Period(dt, freq="M").to_timestamp() if not pd.isna(dt) else pd.NaT,
            "category": getattr(e, "category", ""),
            "amount": amount,
            "unit": unit,
        })

    df = pd.DataFrame(rows)
    # drop rows without a valid date
    df = df.dropna(subset=["month"]).reset_index(drop=True)
    if df.empty:
        st.info("No dated expenses to chart.")
        return

    # Aggregate by month, category (and unit when not converted)
    group_cols = ["month", "category"] if chf_rates is not None else ["unit", "month", "category"]
    agg = df.groupby(group_cols)["amount"].sum().reset_index()

    # Determine global category ordering (use DEFAULT_CATEGORIES first)
    all_cats = sorted(agg["category"].unique())
    ordered = [c for c in DEFAULT_CATEGORIES if c in all_cats]
    ordered += [c for c in all_cats if c not in ordered]

    # Palette consistent with category totals view
    PALETTE = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
    ]
    if len(PALETTE) < len(ordered):
        times = (len(ordered) + len(PALETTE) - 1) // len(PALETTE)
        colors = (PALETTE * times)[: len(ordered)]
    else:
        colors = PALETTE[: len(ordered)]

    color_scale = alt.Scale(domain=ordered, range=colors)

    if chf_rates is not None:
        chart = alt.Chart(agg).mark_bar().encode(
            x=alt.X("month:T", title="Month", axis=alt.Axis(format="%Y-%m", labelAngle=-45)),
            y=alt.Y("amount:Q", title="Amount (CHF)"),
            color=alt.Color("category:N", scale=color_scale, sort=ordered, legend=alt.Legend(title="Category")),
            tooltip=[
                alt.Tooltip("month:T", title="Month", format="%Y-%m"),
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("amount:Q", title="Amount (CHF)", format=".2f"),
            ],
        ).properties(width="container", height=300)
        st.altair_chart(chart, use_container_width=True)
        return

    # Render one chart per currency/unit when no CHF conversion is requested.
    units = sorted(agg["unit"].unique())
    for unit in units:
        sub = agg[agg["unit"] == unit].copy()
        if sub.empty:
            continue
        st.markdown(f"**{unit}**")
        chart = alt.Chart(sub).mark_bar().encode(
            x=alt.X("month:T", title="Month", axis=alt.Axis(format="%Y-%m", labelAngle=-45)),
            y=alt.Y("amount:Q", title=f"Amount ({unit})"),
            color=alt.Color("category:N", scale=color_scale, sort=ordered, legend=alt.Legend(title="Category")),
            tooltip=[
                alt.Tooltip("month:T", title="Month", format="%Y-%m"),
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("amount:Q", title="Amount", format=".2f"),
            ],
        ).properties(width="container", height=300)
        st.altair_chart(chart, use_container_width=True)

def display_settle_suggestions(suggestions_by_unit: Dict[str, List[str]]):
    """Show settle-up suggestions per currency."""
    st.header("Settle Suggestions")
    if suggestions_by_unit:
        for unit, suggestions in suggestions_by_unit.items():
            st.markdown(f"**{unit}**")
            if not suggestions:
                st.write("  Nothing to settle.")
                continue
            for s in suggestions:
                st.write(f"  {s}")
    else:
        st.write("No settle suggestions available.")


def display_settle_suggestions_chf(
    suggestions: List[str],
    grand_total_chf: Optional[float] = None,
    fx_snapshot: Optional[Dict[str, Any]] = None,
    skipped_units: Optional[Dict[str, float]] = None,
):
    """Show settle-up suggestions in CHF only."""
    st.header("Settle Suggestions (CHF)")
    _show_grand_total_chf(grand_total_chf, fx_snapshot=fx_snapshot)
    _show_skipped_units(skipped_units)
    if suggestions:
        for s in suggestions:
            st.write(f"  {s}")
    else:
        st.write("Nothing to settle.")


def display_category_totals(totals_by_unit: Dict[str, Dict[str, float]]):
    """Show total amounts per category, grouped by currency unit.
    Also render a pie chart per currency when data exists. Use a stable color
    mapping so the same category keeps the same color across currencies.
    """
    st.header("Totals per Category")
    if not totals_by_unit:
        st.write("No totals to display.")
        return

    # Build global ordered category list so color mapping is consistent across currencies.
    all_cats = set()
    for unit_map in totals_by_unit.values():
        all_cats.update(unit_map.keys())
    # Start with default categories first, then any other categories sorted
    ordered = [c for c in DEFAULT_CATEGORIES if c in all_cats]
    ordered += sorted([c for c in all_cats if c not in ordered])

    # Define a palette and ensure it covers all categories (cycle if needed)
    PALETTE = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
    ]
    if len(PALETTE) < len(ordered):
        # extend by repeating palette as needed
        times = (len(ordered) + len(PALETTE) - 1) // len(PALETTE)
        colors = (PALETTE * times)[: len(ordered)]
    else:
        colors = PALETTE[: len(ordered)]

    # Color scale configuration for Altair
    color_scale = alt.Scale(domain=ordered, range=colors)

    for unit, cat_map in totals_by_unit.items():
        st.markdown(f"**{unit}**")
        if not cat_map:
            st.write("  No expenses.")
            continue

        # show textual totals and overall sum
        total_amount = sum(float(v) for v in cat_map.values())
        st.write(f"Total: {total_amount:.2f} {unit}")
        # show category totals with percentage share in text
        for cat, total in cat_map.items():
            amt_f = float(total)
            pct = (amt_f / total_amount * 100) if total_amount > 0 else 0.0
            st.write(f"  {cat}: {amt_f:.2f} {unit} ({pct:.1f}%)")

        # Build DataFrame for charting including percent and label fields
        rows = []
        for cat, amt in cat_map.items():
            amt_f = float(amt)
            pct = (amt_f / total_amount * 100) if total_amount > 0 else 0.0
            rows.append({
                "category": cat,
                "amount": amt_f,
                "percent": pct,
                "percent_label": f"{pct:.1f}%"
            })
        df = pd.DataFrame(rows)

        # Avoid rendering empty/zero-only charts
        if df["amount"].sum() <= 0:
            st.info("No positive amounts to chart for this currency.")
            continue

        # Pie chart: arc with consistent color mapping and tooltip including share %
        pie = alt.Chart(df).mark_arc(innerRadius=50).encode(
            theta=alt.Theta(field="amount", type="quantitative"),
            color=alt.Color(field="category", type="nominal", scale=color_scale, legend=alt.Legend(title="Category")),
            tooltip=[
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("amount:Q", title=f"Amount ({unit})", format=".2f"),
                alt.Tooltip("percent:Q", title="Share", format=".1f")
            ]
        ).properties(
            title=f"Category share ({unit})"
        )

        # Render chart without in-chart text labels (percentages shown in the textual list above)
        st.altair_chart(pie, use_container_width=True)


def display_category_totals_chf(
    totals_chf: Dict[str, float],
    grand_total_chf: Optional[float] = None,
    fx_snapshot: Optional[Dict[str, Any]] = None,
    skipped_units: Optional[Dict[str, float]] = None,
):
    """Show totals per category in CHF only, with a pie chart."""
    st.header("Totals per Category (CHF)")
    _show_grand_total_chf(grand_total_chf, fx_snapshot=fx_snapshot)
    _show_skipped_units(skipped_units)
    if not totals_chf:
        st.write("No totals to display.")
        return

    total_amount = float(sum(totals_chf.values()))
    st.write(f"Total: {total_amount:.2f} CHF")
    rows = []
    for cat, amt in totals_chf.items():
        amt_f = float(amt)
        pct = (amt_f / total_amount * 100) if total_amount > 0 else 0.0
        st.write(f"  {cat}: {amt_f:.2f} CHF ({pct:.1f}%)")
        rows.append({"category": cat, "amount": amt_f, "percent": pct})

    df = pd.DataFrame(rows)
    if df["amount"].sum() <= 0:
        st.info("No positive amounts to chart.")
        return

    # Stable category ordering and color mapping.
    all_cats = sorted(df["category"].unique())
    ordered = [c for c in DEFAULT_CATEGORIES if c in all_cats]
    ordered += [c for c in all_cats if c not in ordered]

    PALETTE = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"
    ]
    if len(PALETTE) < len(ordered):
        times = (len(ordered) + len(PALETTE) - 1) // len(PALETTE)
        colors = (PALETTE * times)[: len(ordered)]
    else:
        colors = PALETTE[: len(ordered)]
    color_scale = alt.Scale(domain=ordered, range=colors)

    pie = alt.Chart(df).mark_arc(innerRadius=50).encode(
        theta=alt.Theta(field="amount", type="quantitative"),
        color=alt.Color(
            field="category",
            type="nominal",
            scale=color_scale,
            legend=alt.Legend(title="Category"),
            sort=ordered,
        ),
        tooltip=[
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("amount:Q", title="Amount (CHF)", format=".2f"),
            alt.Tooltip("percent:Q", title="Share", format=".1f"),
        ],
    ).properties(title="Category share (CHF)")
    st.altair_chart(pie, use_container_width=True)


def display_manage_expenses(tracker):
    """
    UI to select, edit and delete an existing expense.
    Expects a tracker instance (src.tracker.ExpenseTracker) with
    methods: list_expenses(), edit_expense(id, **kwargs), delete_expense(id).
    """
    st.header("Edit / Delete Expense")
    exs = tracker.list_expenses()
    if not exs:
        st.info("No expenses recorded.")
        return

    # Build selection options: show id, category, amount and date
    options = {f"#{e.id} {e.category} {e.amount:.2f} {getattr(e, 'date', '')}": e.id for e in exs}
    sel_label = st.selectbox("Select expense", options=list(options.keys()))
    expense_id = options[sel_label]
    expense = next((e for e in exs if e.id == expense_id), None)
    if not expense:
        st.error("Selected expense not found.")
        return

    # Prefill form with existing values
    with st.form(key=f"edit_expense_{expense.id}"):
        amount = st.number_input("Amount", min_value=0.0, format="%.2f", value=float(expense.amount))
        unit_options = ["EUR", "USD", "GBP", "CHF", "other"]
        unit_idx = unit_options.index(getattr(expense, "unit", "EUR")) if getattr(expense, "unit", "EUR") in unit_options else 0
        unit = st.selectbox("Unit / Currency", options=unit_options, index=unit_idx)
        payer = st.selectbox("Payer Name", options=["Alessio", "Morgan"], index=0 if expense.payer == "Alessio" else 1)
        participants = st.multiselect("Participants", options=["Alessio", "Morgan"], default=list(expense.participants))
        categories = tracker.get_categories()
        cat_index = categories.index(expense.category) if expense.category in categories else 0
        category = st.selectbox("Category", options=categories, index=cat_index)
        description = st.text_input("Description", value=getattr(expense, "description", ""))
        try:
            date_prefill = datetime.date.fromisoformat(expense.date) if getattr(expense, "date", "") else datetime.date.today()
        except Exception:
            date_prefill = datetime.date.today()
        date_selected = st.date_input("Date", value=date_prefill)

        # Split mode / shares
        split_mode = "Custom shares" if getattr(expense, "shares", {}) else "Equal split"
        split_mode = st.radio("Split mode", options=["Equal split", "Custom shares"], index=0 if split_mode == "Equal split" else 1)
        shares = {}
        if split_mode == "Custom shares":
            st.write("Enter custom share amounts (must sum to total amount):")
            for p in participants:
                shares[p] = st.number_input(f"Share for {p}", min_value=0.0, format="%.2f",
                                            value=float(expense.shares.get(p, round(amount / max(1, len(participants)), 2))))

        save_btn = st.form_submit_button("Save changes")

        if save_btn:
            # basic validation
            if amount <= 0:
                st.error("Amount must be > 0")
            elif not participants:
                st.error("At least one participant required")
            else:
                shares_final = {}
                if split_mode == "Custom shares":
                    total_shares = round(sum(shares.values()), 2)
                    if abs(total_shares - round(amount, 2)) > 0.01:
                        st.error(f"Custom shares sum to {total_shares:.2f} but amount is {amount:.2f}")
                        return
                    shares_final = {p: round(shares[p], 2) for p in participants}
                updated = tracker.edit_expense(
                    expense_id=expense.id,
                    amount=round(amount, 2),
                    payer=payer,
                    participants=participants,
                    category=category,
                    description=description,
                    unit=unit,
                    shares=shares_final,
                    date=date_selected.isoformat() if hasattr(date_selected, "isoformat") else ""
                )
                if updated:
                    st.success("Expense updated.")
                    # refresh so selector and list reflect changes
                    try:
                        _trigger_rerun()
                    except Exception:
                        pass
                else:
                    st.error("Failed to update expense.")

    # Delete UI (separate to avoid accidental deletes)
    st.markdown("---")
    st.write("Delete this expense")
    delete_confirm = st.checkbox("I confirm I want to delete this expense")
    if st.button("Delete expense") and delete_confirm:
        try:
            ok = tracker.delete_expense(expense.id)
        except Exception as exc:
            st.error(f"Error deleting expense: {exc}")
            ok = False
        if ok:
            st.success("Expense deleted.")
            try:
                _trigger_rerun()
            except Exception:
                pass
        else:
            st.error("Failed to delete expense. Check the server logs for details.")
