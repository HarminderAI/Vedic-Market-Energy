from flask import Flask, request
from threading import Thread
import main  # Assuming your main logic is in main.py

app = Flask('')

@app.route('/')
def home():
    # Check if the URL has ?mode=eod
    mode = request.args.get('mode')
    
    if mode == 'eod':
        # Trigger the EOD logic from your main file
        report = main.calculate_eod_performance()
        main.send_telegram_msg(report) # Ensure you have a sender function
        return "EOD Report Sent Successfully!", 200
    
    # Default heartbeat response
    return "Bot is Alive!", 200

def run():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run)
    t.start()
