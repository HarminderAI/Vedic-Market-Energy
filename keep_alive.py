# ==========================================================
# 🔁 KEEP ALIVE — RENDER SAFE + NON-BLOCKING (2026)
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
# eval() handles the stringified JSON from environment variables
SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

STATE_KEY = "last_eod_run"

# ----------------------------------------------------------
# FLASK APP
# ----------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health_check():
    """Render uses this to verify the service is alive."""
    return "OK", 200

# ----------------------------------------------------------
# STATE HELPERS (PERSISTENT VIA GOOGLE SHEETS)
# ----------------------------------------------------------
def get_state_ws():
    """Helper to get sheet client and worksheet dynamically to avoid stale connections."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(eval(SERVICE_JSON), scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(GOOGLE_SHEET_ID)
    return sheet.worksheet("state")

def get_last_eod_run():
    try:
        ws = get_state_ws()
        rows = ws.get_all_records()
        for r in rows:
            if r.get("key") == STATE_KEY:
                return r.get("value")
    except Exception as e:
        print(f"Error fetching state: {e}")
    return None

def set_last_eod_run(date_str):
    try:
        ws = get_state_ws()
        rows = ws.get_all_records()
        for idx, r in enumerate(rows, start=2):
            if r.get("key") == STATE_KEY:
                ws.update_cell(idx, 2, date_str)
                return
        ws.append_row([STATE_KEY, date_str])
    except Exception as e:
        print(f"Error saving state: {e}")

# ----------------------------------------------------------
# EOD CALLBACK RUNNER
# ----------------------------------------------------------
def eod_runner(callback):
    """Background loop that triggers the EOD task once per IST day."""
    while True:
        try:
            today_ist = datetime.now(IST).date().isoformat()
            last_run = get_last_eod_run()

            if last_run != today_ist:
                print(f"⏰ Triggering EOD Task for {today_ist}...")
                callback()
                set_last_eod_run(today_ist)
        except Exception as e:
            print("EOD runner error:", e)

        # Check every 30 minutes
        time.sleep(1800)

# ----------------------------------------------------------
# PUBLIC ENTRY POINT
# ----------------------------------------------------------
def keep_alive(eod_callback=None):
    """
    Starts Flask server in a BACKGROUND thread to prevent blocking.
    Allows the main script to continue execution.
    """
    # 1. Start EOD Scheduler (if provided)
    if eod_callback:
        eod_thread = threading.Thread(
            target=eod_runner,
            args=(eod_callback,),
            daemon=True
        )
        eod_thread.start()
        print("✅ EOD Scheduler started in background.")

    # 2. Start Flask Server in background
    # use_reloader=False is mandatory when running in a thread
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()
    print(f"✅ Flask Web Server started in background on port {PORT}.")
    
