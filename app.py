"""
app.py - minimal entrypoint for Streamlit app

Keep this file tiny so streamlit can import it without side-effects.
Run the app with:
    streamlit run app.py

This module simply delegates to src.ui.dashboard.main().

"""
import os
import json as _json
try:
    # If running on Streamlit Cloud, transfer secrets to env vars so backend can read them
    import streamlit as _st
    _secrets = getattr(_st, "secrets", {}) or {}
    for _k in ("GOOGLE_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SERVICE_ACCOUNT_FILE"):
        if _k in _secrets and _secrets[_k] and _k not in os.environ:
            os.environ[_k] = _secrets[_k]
    # Also support the standard Streamlit table-style service account secret:
    # [gcp_service_account] ...fields...
    if "GOOGLE_SERVICE_ACCOUNT_JSON" not in os.environ and "gcp_service_account" in _secrets:
        _sa = _secrets["gcp_service_account"]
        if _sa:
            try:
                _sa_dict = dict(_sa)
                os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = _json.dumps(_sa_dict)
            except Exception:
                pass
except Exception:
    # keep import-time side-effects minimal if streamlit isn't available
    pass

from src.ui import dashboard


def main():
    dashboard.main()


if __name__ == "__main__":
    main()
