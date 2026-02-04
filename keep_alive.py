# ==========================================================
# 🔁 KEEP ALIVE — RENDER SAFE + PERSISTENT MEMORY (2026)
# ==========================================================

import os
import threading
import time
from datetime import datetime

from flask import Flask
import pytz
import gspread
from google.oauth2.service_account import Credentials

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------

PORT = int(os.getenv("PORT", 10000))
IST = pytz.timezone("Asia/Kolkata")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

STATE_KEY = "last_eod_run"

# ----------------------------------------------------------
# GOOGLE SHEETS CLIENT
# ----------------------------------------------------------

def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(
        eval(SERVICE_JSON),
        scopes=scopes
    )
    return gspread.authorize(creds)

gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)
state_ws = sheet.worksheet("state")

# ----------------------------------------------------------
# FLASK APP
# ----------------------------------------------------------

app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health_check():
    return "OK", 200

# ----------------------------------------------------------
# STATE HELPERS (PERSISTENT)
# ----------------------------------------------------------

def get_last_eod_run():
    rows = state_ws.get_all_records()
    for r in rows:
        if r.get("key") == STATE_KEY:
            return r.get("value")
    return None

def set_last_eod_run(date_str):
    rows = state_ws.get_all_records()

    for idx, r in enumerate(rows, start=2):  # row 1 = header
        if r.get("key") == STATE_KEY:
            state_ws.update_cell(idx, 2, date_str)
            return

    # If not found, append
    state_ws.append_row([STATE_KEY, date_str])

# ----------------------------------------------------------
# EOD CALLBACK RUNNER
# ----------------------------------------------------------

def eod_runner(callback):
    """
    Runs the EOD callback ONCE per IST day.
    Uses Google Sheets for persistent memory.
    """
    while True:
        try:
            today_ist = datetime.now(IST).date().isoformat()
            last_run = get_last_eod_run()

            if last_run != today_ist:
                callback()
                set_last_eod_run(today_ist)

        except Exception as e:
            # EOD must NEVER crash the service
            print("EOD runner error:", e)

        # Check once per hour
        time.sleep(3600)

# ----------------------------------------------------------
# PUBLIC ENTRY POINT
# ----------------------------------------------------------

def keep_alive(eod_callback=None):
    """
    Starts Flask server (for Render)
    + optional EOD callback with persistent memory
    """

    if eod_callback:
        t = threading.Thread(
            target=eod_runner,
            args=(eod_callback,),
            daemon=True
        )
        t.start()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )
