from flask import Flask, request
from threading import Thread

app = Flask('')

# This variable will store the EOD function passed from main.py
eod_trigger_func = None

@app.route('/')
def home():
    # Detect the ?mode=eod parameter
    mode = request.args.get('mode')
    
    if mode == 'eod' and eod_trigger_func:
        # Execute the function passed from main.py
        eod_trigger_func()
        return "EOD Verification Triggered and Sent!", 200
    
    return "Vedic Bot Heartbeat: Active", 200

def run():
    # Port 10000 is standard for Render
    app.run(host='0.0.0.0', port=10000)

def keep_alive(callback):
    """
    Starts the web server and accepts a callback function 
    to handle EOD logic without circular imports.
    """
    global eod_trigger_func
    eod_trigger_func = callback
    t = Thread(target=run)
    t.start()
