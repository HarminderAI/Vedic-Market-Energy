from flask import Flask
from threading import Thread
import os  # <--- THIS WAS MISSING!

app = Flask('')

@app.route('/')
def home():
    return "Vedic Bot is Awake!"

def run():
    # Render needs to know which port to use
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
