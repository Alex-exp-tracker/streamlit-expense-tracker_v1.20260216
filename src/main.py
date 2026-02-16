from streamlit import st
from src.tracker import ExpenseTracker
from src.ui.dashboard import display_dashboard
from src.ui.components import add_expense_form, display_expenses, display_balances, display_settle_suggestions

def main():
    st.title("Expense Tracker")
    tracker = ExpenseTracker()

    menu = ["Add Expense", "List Expenses", "Show Balances", "Show Settle Suggestions", "Clear All Expenses"]
    choice = st.sidebar.selectbox("Select an option", menu)

    if choice == "Add Expense":
        add_expense_form(tracker)
    elif choice == "List Expenses":
        display_expenses(tracker)
    elif choice == "Show Balances":
        display_balances(tracker)
    elif choice == "Show Settle Suggestions":
        display_settle_suggestions(tracker)
    elif choice == "Clear All Expenses":
        if st.button("Clear All Expenses"):
            tracker.clear()
            st.success("All expenses cleared.")

if __name__ == "__main__":
    main()