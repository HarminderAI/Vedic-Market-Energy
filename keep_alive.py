# ==========================================================
# 🔁 KEEP ALIVE — RENDER SAFE + NON-BLOCKING (2026 STABLE)
# ==========================================================

import os
import json
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
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

STATE_KEY = "last_eod_run"

# ----------------------------------------------------------
# FLASK APP (Render Health Check)
# ----------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def health_check():
    return "OK", 200

# ----------------------------------------------------------
# GOOGLE SHEETS — SAFE REFRESH
# ----------------------------------------------------------
def get_sheet():
    """
    Always returns a fresh Google Sheet handle.
    Prevents stale OAuth sessions in long-running apps.
    """
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(
        SERVICE_JSON, scopes=scopes
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(GOOGLE_SHEET_ID)

def get_state_ws():
    sheet = get_sheet()
    try:
        return sheet.worksheet("state")
    except:
        ws = sheet.add_worksheet("state", rows=100, cols=2)
        ws.append_row(["key", "value"])
        return ws

# ----------------------------------------------------------
# STATE HELPERS
# ----------------------------------------------------------
def get_last_eod_run():
    """
    Reads last EOD execution date from Google Sheets.
    """
    try:
        ws = get_state_ws()
        for row in ws.get_all_records():
            if row.get("key") == STATE_KEY:
                return row.get("value")
    except Exception as e:
        print("⚠️ Error reading EOD state:", e)
    return None

def set_last_eod_run(date_str):
    """
    Saves EOD execution date to Google Sheets.
    """
    try:
        ws = get_state_ws()
        rows = ws.get_all_records()

        for idx, row in enumerate(rows, start=2):
            if row.get("key") == STATE_KEY:
                ws.update_cell(idx, 2, date_str)
                return

        ws.append_row([STATE_KEY, date_str])

    except Exception as e:
        print("⚠️ Error writing EOD state:", e)

# ----------------------------------------------------------
# EOD CALLBACK RUNNER (ONCE PER IST DAY)
# ----------------------------------------------------------
def eod_runner(callback):
    """
    Runs EOD callback exactly ONCE per IST day.
    Independent of restarts, redeploys, or crashes.
    """
    while True:
        try:
            today = datetime.now(IST).date().isoformat()
            last_run = get_last_eod_run()

            if last_run != today:
                print(f"⏰ Triggering EOD Task for {today}")
                callback()
                set_last_eod_run(today)

        except Exception as e:
            print("❌ EOD runner error:", e)

        # Check every 30 minutes
        time.sleep(1800)

# ----------------------------------------------------------
# PUBLIC ENTRY POINT
# ----------------------------------------------------------
def keep_alive(eod_callback=None):
    """
    Starts:
    1️⃣ Flask health server (Render requirement)
    2️⃣ Optional EOD scheduler (non-blocking, internal)
    """

    # 1️⃣ Start EOD Scheduler
    if eod_callback:
        threading.Thread(
            target=eod_runner,
            args=(eod_callback,),
            daemon=True
        ).start()
        print("✅ EOD Scheduler started in background.")

    # 2️⃣ Start Flask Server (non-blocking)
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=PORT,
            debug=False,
            use_reloader=False
        ),
        daemon=True
    ).start()

    print(f"✅ Flask Web Server started on port {PORT}.")
