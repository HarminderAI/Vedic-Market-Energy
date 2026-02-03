from flask import Flask, request
from threading import Thread
import os

app = Flask(__name__)

# Store the EOD callback from main.py
eod_trigger_func = None

# Optional secret to protect EOD trigger
EOD_SECRET = os.getenv("EOD_SECRET")  # optional but recommended

@app.route("/")
def home():
    mode = request.args.get("mode")
    secret = request.args.get("secret")

    # Explicit EOD trigger
    if mode == "eod" and eod_trigger_func:
        # Optional protection
        if EOD_SECRET and secret != EOD_SECRET:
            return "Unauthorized", 403

        try:
            eod_trigger_func()
            return "EOD Verification Triggered and Sent!", 200
        except Exception as e:
            return f"EOD Trigger Failed: {e}", 500

    return "Vedic Bot Heartbeat: Active", 200

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive(callback):
    """
    Starts the web server and registers a callback
    for EOD logic without circular imports.
    """
    global eod_trigger_func
    eod_trigger_func = callback

    t = Thread(target=run)
    t.daemon = True  # important for clean shutdowns
    t.start()
