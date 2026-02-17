# Streamlit Expense Tracker

Expense tracker built with Streamlit. It supports adding, editing, listing, and analyzing shared expenses.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Durable cloud persistence (Google Sheets)

When deployed on Streamlit Cloud, local files are temporary. For indefinite storage in spreadsheet format, configure Google Sheets.

### Required secrets

- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON` (recommended), or `gcp_service_account` table format

Optional for local development:

- `GOOGLE_SERVICE_ACCOUNT_FILE`

### Spreadsheet layout

The app writes to two worksheets in the target spreadsheet:

- `expenses`: one row per expense (`id`, `amount`, `payer`, `participants`, `category`, `description`, `unit`, `shares_json`, `date`)
- `meta`: key/value rows (`next_id`, `categories`)

### Security notes

- Keep service-account credentials only in Streamlit secrets, never in git.
- Use a dedicated service account with access only to the expense spreadsheet.
- Cell writes use Google Sheets `RAW` mode, so user input is stored as plain data (not executed as formulas).

A template is included at `.streamlit/secrets.toml.example`.
