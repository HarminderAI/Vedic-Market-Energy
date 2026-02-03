import os
from flask import Flask, request
from threading import Thread

app = Flask(__name__)

# ----------------------------------------------------------
# CONFIG
# ----------------------------------------------------------

EOD_SECRET = os.getenv("EOD_SECRET")  # REQUIRED for EOD trigger

# This will store the callback passed from main.py
eod_trigger_func = None


# ----------------------------------------------------------
# ROUTES
# ----------------------------------------------------------

@app.route("/", methods=["GET", "HEAD"])
def heartbeat():
    """
    Primary Render heartbeat.
    Keeps the service alive and allows optional EOD trigger.
    """

    mode = request.args.get("mode")
    secret = request.args.get("secret")

    # ---- Secure EOD Trigger ----
    if mode == "eod":
        if not EOD_SECRET or secret != EOD_SECRET:
            return "❌ Unauthorized EOD trigger", 403

        if eod_trigger_func:
            try:
                eod_trigger_func()
                return "✅ EOD Report Triggered", 200
            except Exception as e:
                return f"⚠️ EOD Error: {e}", 500

        return "⚠️ EOD function not registered", 500

    # ---- Normal Heartbeat ----
    return "🫀 Vedic Institutional Bot: Alive", 200


# ----------------------------------------------------------
# SERVER RUNNER
# ----------------------------------------------------------

def run():
    """
    Starts Flask server on Render-required port.
    """
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ----------------------------------------------------------
# PUBLIC API
# ----------------------------------------------------------

def keep_alive(callback):
    """
    Starts the Flask heartbeat server in a daemon thread
    and registers an EOD callback safely.

    Args:
        callback (callable): Function to execute on ?mode=eod
    """
    global eod_trigger_func
    eod_trigger_func = callback

    t = Thread(target=run)
    t.daemon = True  # CRITICAL for Render restarts
    t.start()
