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
    # Digging into the V2 structure
    # The actual data is usually inside 'data' -> 'panchang'
    inner_data = data.get('data', {})
    panchang = inner_data.get('panchang', {})
    
    # Safely extract names from the lists
    tithi_list = panchang.get('tithi', [])
    nakshatra_list = panchang.get('nakshatra', [])
    
    tithi = tithi_list[0].get('name', 'Unknown') if tithi_list else "Unknown"
    nakshatra = nakshatra_list[0].get('name', 'Unknown') if nakshatra_list else "Unknown"
    
    # If the API returned an error, the 'tithi' will be 'Unknown'
    # Check if there is an error message in the response
    if 'errors' in data:
        tithi = "API Error"
        nakshatra = data['errors'][0].get('message', 'Check Logs')

    weekday_idx = datetime.datetime.now().weekday()

    # Sector Ratings (out of 5 stars)
    it_rating = "⭐⭐"
    banking_rating = "⭐⭐"
    pharma_rating = "⭐⭐"

    # Logic remains the same
    if weekday_idx == 2: it_rating = "⭐⭐⭐⭐"
    if nakshatra in ["Revati", "Jyeshtha", "Ashlesha"]: it_rating = "⭐⭐⭐⭐⭐"
    
    if weekday_idx == 3: banking_rating = "⭐⭐⭐⭐"
    if nakshatra == "Pushya": banking_rating = "⭐⭐⭐⭐⭐"

    if weekday_idx == 0: pharma_rating = "⭐⭐⭐⭐"
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
