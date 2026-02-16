"""
app.py - minimal entrypoint for Streamlit app

Keep this file tiny so streamlit can import it without side-effects.
Run the app with:
    streamlit run app.py

This module simply delegates to src.ui.dashboard.main().

"""
import os
try:
    # If running on Streamlit Cloud, transfer secrets to env vars so backend can read them
    import streamlit as _st
    _secrets = getattr(_st, "secrets", {}) or {}
    for _k in ("GOOGLE_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SERVICE_ACCOUNT_FILE"):
        if _k in _secrets and _secrets[_k] and _k not in os.environ:
            os.environ[_k] = _secrets[_k]
except Exception:
    # keep import-time side-effects minimal if streamlit isn't available
    pass

from src.ui import dashboard


def main():
    dashboard.main()


if __name__ == "__main__":
    main()