from flask import Flask, request
from threading import Thread
import main # Import your main script logic

app = Flask('')

@app.route('/')
def home():
    # Check if the URL is hit with ?mode=eod
    mode = request.args.get('mode')
    
    if mode == 'eod':
        # Trigger the EOD logic directly
        eod_report = main.calculate_eod_performance()
        main.send_telegram_msg(eod_report)
        return "EOD Verification Sent!", 200
    
    # Check if hit with ?mode=morning (Optional, for manual triggers)
    if mode == 'morning':
        main.main()
        return "Morning Report Sent!", 200

    # Default heartbeat response for Render
    return "Vedic Bot is Alive!", 200

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()
