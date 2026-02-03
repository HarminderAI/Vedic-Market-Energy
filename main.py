from keep_alive import keep_alive
import os
import requests
import datetime
import time

# Start the 'heartbeat' server
keep_alive()

# --- CONFIGURATION (From Secrets) ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
CLIENT_ID = os.environ.get('PROKERALA_CLIENT_ID')
CLIENT_SECRET = os.environ.get('PROKERALA_CLIENT_SECRET')

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
    url = "https://api.prokerala.com/v2/astrology/panchang"
    params = {
        # Using a slightly simpler date format for the API
        'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+05:30'),
        'coordinates': '23.1765,75.7885', # Ujjain
        'ayanamsa': 1
    }
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get(url, params=params, headers=headers)
    
    # DEBUG: This will show us the REAL error in Render Logs
    print("RAW API RESPONSE:", response.text) 
    
    return response.json()

def generate_market_report(data):
    # Based on your logs, the structure is: data -> tithi/nakshatra
    # There is NO 'panchang' middle-man key in this specific response.
    
    inner_data = data.get('data', {})
    
    # 1. Extract Tithi
    tithi_list = inner_data.get('tithi', [])
    tithi = tithi_list[0].get('name', 'Determining...') if tithi_list else "Unknown"
    
    # 2. Extract Nakshatra
    nakshatra_list = inner_data.get('nakshatra', [])
    nakshatra = nakshatra_list[0].get('name', 'Determining...') if nakshatra_list else "Unknown"
    
    # 3. Extract Yoga (just for a better report)
    yoga_list = inner_data.get('yoga', [])
    yoga = yoga_list[0].get('name', 'N/A') if yoga_list else "N/A"

    weekday_idx = datetime.datetime.now().weekday()
    
    # --- Sector Logic ---
    it_rating = "⭐⭐"
    banking_rating = "⭐⭐"
    pharma_rating = "⭐⭐"

    # Tuesday (Mars) + Magha (Ketu) focus:
    # Magha is generally good for established 'Thrones' or large-cap Banking.
    if nakshatra == "Magha": banking_rating = "⭐⭐⭐⭐"
    if tithi == "Dwitiya": pharma_rating = "⭐⭐⭐"

    report = (
        f"🏛️ *Vedic Sector Heatmap* 🏛️\n"
        f"📅 Date: {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"✨ Tithi: {tithi} | ⭐ Nakshatra: {nakshatra}\n"
        f"🌀 Yoga: {yoga}\n"
        f"--------------------------\n"
        f"💻 IT & Tech: {it_rating}\n"
        f"🏦 Banking/NBFC: {banking_rating}\n"
        f"💊 Pharmaceuticals: {pharma_rating}\n"
        f"--------------------------\n"
        f"💡 *Astro-Tip:* Today's Magha Nakshatra favors legacy institutions and 'old money' sectors.\n"
        f"--------------------------\n"
        f"⚠️ *Disclaimer:* Educational Study only. Not SEBI advice."
    )
    return report
def send_telegram_msg(text):
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
