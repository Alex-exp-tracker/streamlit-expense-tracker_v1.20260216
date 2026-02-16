# Streamlit Expense Tracker

This project is a Streamlit application designed to help users track their expenses efficiently. It allows users to add expenses, view balances, and get suggestions on how to settle debts among participants.

## Features

- **Add Expense**: Users can input the amount, payer, participants, category, and description of an expense.
- **List Expenses**: View all recorded expenses with details such as category, amount, payer, and participants.
- **Show Balances**: Calculate and display net balances for each participant, indicating who owes money and who is owed.
- **Settle Suggestions**: Generate suggestions for settling debts among participants based on their balances.
- **Clear Expenses**: Option to clear all recorded expenses.

## Project Structure

```
streamlit-expense-tracker
├── app.py                # Entry point for the Streamlit application
├── src
│   ├── main.py          # Main logic for running the Streamlit app
│   ├── tracker.py       # ExpenseTracker class for managing expenses
│   ├── models.py        # Expense data model using dataclasses
│   ├── storage.py       # Data storage and retrieval management (local JSON)
│   └── ui
│       ├── dashboard.py  # Streamlit components for the dashboard
│       └── components.py  # Reusable Streamlit components for user input
├── data
│   └── expenses_data.json # JSON file for storing expense data (fallback)
├── tests
│   └── test_tracker.py   # Unit tests for the tracker module
├── requirements.txt      # List of dependencies for the project
├── .streamlit
│   └── config.toml      # Configuration settings for the Streamlit app
└── README.md             # Documentation for the project
```

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd streamlit-expense-tracker
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

To run the Streamlit application, execute the following command in your terminal:

```bash
streamlit run app.py
```

Open your web browser and navigate to `http://localhost:8501` to access the application.

## Google Sheets backend (optional)

You can configure the app to persist and read expenses from a Google Sheet so the app stays up-to-date across devices and browsers.

Setup summary:

- Create a Google Cloud project and a Service Account with the "Service Account Key" JSON.
- Create a Google Sheet and note its Sheet ID (the long id in the URL).
- Share the sheet with the service account email (editor access).
- Provide the app with the sheet id and credentials via environment variables or Streamlit secrets.

Environment variables (choose one of the credential approaches):

- `GOOGLE_SHEET_ID` (required): the target Google Sheet ID (from the sheet URL).
- `GOOGLE_SERVICE_ACCOUNT_FILE` (optional): path to the service account JSON file on disk.
- `GOOGLE_SERVICE_ACCOUNT_JSON` (optional): the full JSON content of the service account key (useful for cloud secrets).

Notes:

- If neither `GOOGLE_SERVICE_ACCOUNT_FILE` nor `GOOGLE_SERVICE_ACCOUNT_JSON` is set, the code will try to use default credentials (e.g. `GOOGLE_APPLICATION_CREDENTIALS`).
- On Streamlit Cloud, add `GOOGLE_SERVICE_ACCOUNT_JSON` and `GOOGLE_SHEET_ID` to the app secrets (Dashboard -> Secrets) and the app will use those.

Behavior:

- When `GOOGLE_SHEET_ID` is present and the Google Sheets credentials are usable, the app reads/writes data to two worksheets inside the sheet:
  - `expenses` — rows of saved expenses (id, amount, payer, participants, category, description, unit, shares, date)
  - `meta` — small key/value pairs (`next_id` and `categories`)
- If Google Sheets is not available the app falls back to the local `data/expenses_data.json` file.

Quick example (local run with env vars on Windows PowerShell):

```powershell
setx GOOGLE_SHEET_ID "<your-sheet-id>"
setx GOOGLE_SERVICE_ACCOUNT_JSON "{...paste-json...}"
pip install -r requirements.txt
streamlit run app.py
```

On Streamlit Cloud, add the same keys to the app secrets and deploy — the app will read/write the sheet automatically.

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any enhancements or bug fixes.

## License

This project is licensed under the MIT License. See the LICENSE file for details.
