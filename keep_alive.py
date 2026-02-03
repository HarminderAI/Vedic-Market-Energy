from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Vedic Bot is Awake!"

def run():
    # Render uses a dynamic port; this line auto-detects it
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
