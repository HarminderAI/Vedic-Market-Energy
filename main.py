from keep_alive import keep_alive
import os

# Start the 'heartbeat' server
keep_alive()

import os
import requests
import datetime
import time

# --- CONFIGURATION (From Replit Secrets) ---
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
CLIENT_ID = os.environ['PROKERALA_CLIENT_ID']
CLIENT_SECRET = os.environ['PROKERALA_CLIENT_SECRET']

def get_prokerala_token():
    """Authenticates with Prokerala to get a temporary Access Token."""
    url = "https://api.prokerala.com/token"
    data = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET
    }
    response = requests.post(url, data=data)
    return response.json().get('access_token')

def get_panchang_data(token):
    """Fetches real-time Tithi and Nakshatra from Prokerala."""
    # Using Ujjain coordinates as a standard for Indian Market logic
    url = "https://api.prokerala.com/v2/astrology/panchang"
    params = {
        'datetime': datetime.datetime.now().isoformat(),
        'coordinates': '23.1765,75.7885', # Ujjain, India
        'ayanamsa': 1 # Lahiri Ayanamsa
    }
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get(url, params=params, headers=headers)
    return response.json()

def generate_market_report(data):
    panchang = data.get('data', {}).get('panchang', {})
    tithi = panchang.get('tithi', [{}])[0].get('name', 'Unknown')
    nakshatra = panchang.get('nakshatra', [{}])[0].get('name', 'Unknown')
    weekday_idx = datetime.datetime.now().weekday()

    # Sector Ratings (out of 5 stars)
    it_rating = "⭐⭐"
    banking_rating = "⭐⭐"
    pharma_rating = "⭐⭐"

    # 1. IT Sector Logic (Mercury/Rahu)
    # Wednesday is Mercury's day. Nakshatras like Ashlesha or Revati boost it.
    if weekday_idx == 2: it_rating = "⭐⭐⭐⭐" # Wednesday
    if nakshatra in ["Revati", "Jyeshtha"]: it_rating = "⭐⭐⭐⭐⭐"

    # 2. Banking Sector Logic (Jupiter)
    # Thursday is Jupiter's day. Pushya is the best Nakshatra for wealth.
    if weekday_idx == 3: banking_rating = "⭐⭐⭐⭐" # Thursday
    if nakshatra == "Pushya": banking_rating = "⭐⭐⭐⭐⭐"

    # 3. Pharma Sector Logic (Moon/Jupiter)
    # Monday is Moon's day. 
    if weekday_idx == 0: pharma_rating = "⭐⭐⭐⭐" # Monday
    if tithi in ["Purnima", "Ekadashi"]: pharma_rating = "⭐⭐⭐⭐⭐"

    report = (
        f"🏛️ *Vedic Sector Heatmap* 🏛️\n"
        f"📅 Date: {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"✨ Tithi: {tithi} | ⭐ Nakshatra: {nakshatra}\n"
        f"--------------------------\n"
        f"💻 IT & Tech: {it_rating}\n"
        f"🏦 Banking/NBFC: {banking_rating}\n"
        f"💊 Pharmaceuticals: {pharma_rating}\n"
        f"--------------------------\n"
        f"💡 *Astro-Tip:* " + 
        ("Avoid high-frequency trades today (Mercury unstable)." if weekday_idx == 2 and tithi == "Amavasya" else "Auspicious day for long-term SIPs.") +
        f"\n--------------------------\n"
        f"⚠️ *Disclaimer:* Educational Study only. Not SEBI advice."
    )
    return report

def send_telegram_msg(text):
    """Sends the final report to your Telegram Channel."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, data=payload)

def main():
    print("Starting Vedic Finance Bot...")
    try:
        token = get_prokerala_token()
        data = get_panchang_data(token)
        report = generate_market_report(data)
        send_telegram_msg(report)
        print("Success! Report sent to Telegram.")
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == "__main__":
    main()
    
