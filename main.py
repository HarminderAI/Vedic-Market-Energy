from keep_alive import keep_alive
import os
import requests
import datetime
import time

# Start the 'heartbeat' server for Render 24/7 uptime
keep_alive()

# --- CONFIGURATION (From Render Environment Variables) ---
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

def get_combined_data(token):
    """Fetches Panchang, Planet Positions, and Rahu Kaal in one go."""
    # Using Ujjain coordinates for Indian Market standard
    url = "https://api.prokerala.com/v2/astrology/panchang"
    params = {
        'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+05:30'),
        'coordinates': '23.1765,75.7885', 
        'ayanamsa': 1,
        # We include extra dimensions for our advanced features
        'la-dimension': 'planet-position,rahu-kaal' 
    }
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get(url, params=params, headers=headers)
    return response.json()

def generate_market_report(data):
    inner_data = data.get('data', {})
    
    # 1. Extract Basic Panchang
    tithi_list = inner_data.get('tithi', [])
    nakshatra_list = inner_data.get('nakshatra', [])
    tithi = tithi_list[0].get('name', 'N/A') if tithi_list else "Unknown"
    nakshatra = nakshatra_list[0].get('name', 'N/A') if nakshatra_list else "Unknown"
    
    # 2. Extract Opening Sentiment (Moon Sign vs Day Lord)
    moon_sign_list = inner_data.get('moon_sign', [{}])
    moon_sign = moon_sign_list[0].get('name', 'Unknown')
    weekday = datetime.datetime.now().weekday()
    
    positive_signs = {0: ["Cancer", "Taurus"], 1: ["Aries", "Leo"], 2: ["Virgo", "Taurus"], 
                      3: ["Sagittarius", "Pisces"], 4: ["Libra", "Taurus"]}
    
    sentiment = "🚀 Bullish (Gap-Up)" if moon_sign in positive_signs.get(weekday, []) else "📉 Cautious (Gap-Down)"
    if moon_sign == "Unknown": sentiment = "⚖️ Sideways/Neutral"

    # 3. Extract Rahu Kaal Window
    rk_list = inner_data.get('rahu_kaal', [{}])
    try:
        rk_start = datetime.datetime.fromisoformat(rk_list[0].get('start')).strftime('%I:%M %p')
        rk_end = datetime.datetime.fromisoformat(rk_list[0].get('end')).strftime('%I:%M %p')
        rahu_kaal = f"{rk_start} - {rk_end}"
    except:
        rahu_kaal = "Check Daily Chart"

    # 4. Mercury Retrograde Check
    planets = inner_data.get('planet_positions', [])
    mercury_vakra = any(p.get('name') == 'Mercury' and p.get('is_retrograde') for p in planets)

    # 5. Advanced 9-Sector Score Logic
    sectors = {
        "PSU & Energy (Sun)": 3, "FMCG (Moon)": 3, "Banking (Jupiter)": 3,
        "IT & Tech (Mercury)": 3, "Real Estate (Mars)": 3, "Luxury (Venus)": 3,
        "Metals (Saturn)": 3, "Aviation (Rahu)": 3, "Pharma (Ketu)": 3
    }

    # Dynamic Adjustments
    if nakshatra == "Magha": 
        sectors["Banking (Jupiter)"] += 1
        sectors["Pharma (Ketu)"] += 2
    if mercury_vakra: 
        sectors["IT & Tech (Mercury)"] -= 1
    if nakshatra in ["Pushya", "Anuradha"]: 
        sectors["Metals (Saturn)"] += 2

    heatmap_text = ""
    for sector, score in sectors.items():
        heatmap_text += f"{sector}: {'⭐' * min(score, 5)}\n"

    # 6. Final Report Assembly
    report = (
        f"🏛️ *Vedic Financial Heatmap* 🏛️\n"
        f"📅 Date: {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"✨ Tithi: {tithi} | ⭐ Nakshatra: {nakshatra}\n"
        f"🌙 Moon Sign: {moon_sign}\n"
        f"--------------------------\n"
        f"🎭 *Opening Sentiment:* {sentiment}\n"
        f"🚫 *Rahu Kaal:* {rahu_kaal}\n"
        f"--------------------------\n"
        f"{heatmap_text}"
        f"--------------------------\n"
        f"💡 *Astro-Tip:* " + ("⚠️ Mercury Retrograde active. Watch for tech glitches." if mercury_vakra else "Favorable day for institutional buying.") +
        f"\n--------------------------\n"
        f"⚠️ *Educational only. Not SEBI advice.*"
    )
    return report

def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    requests.post(url, data=payload)

def main():
    print("Vedic Bot Processing...")
    try:
        token = get_prokerala_token()
        data = get_combined_data(token)
        report = generate_market_report(data)
        send_telegram_msg(report)
        print("Success! Automated report deployed.")
    except Exception as e:
        print(f"Deployment Error: {e}")

if __name__ == "__main__":
    main()
