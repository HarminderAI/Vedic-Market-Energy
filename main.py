from keep_alive import keep_alive
import os
import requests
import datetime
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import finnhub

# Start the 'heartbeat' server
keep_alive()

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
CLIENT_ID = os.environ.get('PROKERALA_CLIENT_ID')
CLIENT_SECRET = os.environ.get('PROKERALA_CLIENT_SECRET')
FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY')
AYANAMSA_MODE = 1 

finnhub_client = finnhub.Client(api_key=FINNHUB_KEY)

def get_prokerala_token():
    url = "https://api.prokerala.com/token"
    data = {'grant_type': 'client_credentials', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}
    response = requests.post(url, data=data)
    return response.json().get('access_token')

def get_market_intelligence():
    """Fetches Technicals and Sentiment with robust error handling."""
    try:
        nifty = yf.download("^NSEI", period="60d", interval="1d", progress=False)
        vix = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        
        nifty['RSI'] = ta.rsi(nifty['Close'], length=14)
        
        # Safe extraction to prevent NoneType errors
        latest_rsi = float(nifty['RSI'].iloc[-1]) if not nifty['RSI'].empty else 50.0
        latest_vix = float(vix['Close'].iloc[-1]) if not vix.empty else 15.0
        
        sentiment_data = finnhub_client.news_sentiment('AAPL')
        bullish_pct = sentiment_data.get('sentiment', {}).get('bullishPercent', 0.5)
        
        return round(latest_rsi, 2), round(latest_vix, 2), bullish_pct
    except Exception as e:
        print(f"Market Data Warning: {e}")
        return 50.0, 15.0, 0.5

def get_vedic_data(token):
    url = "https://api.prokerala.com/v2/astrology/panchang"
    params = {
        'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+05:30'),
        'coordinates': '23.1765,75.7885',
        'ayanamsa': AYANAMSA_MODE,
        'la-dimension': 'planet-position,rahu-kaal,ashtakavarga,planetary-strength,muhurta'
    }
    headers = {'Authorization': f'Bearer {token}'}
    return requests.get(url, params=params, headers=headers).json()

def generate_ultimate_report(vedic, rsi, vix, sentiment):
    inner = vedic.get('data', {})
    tithi = inner.get('tithi', [{}])[0].get('name', 'N/A')
    nakshatra = inner.get('nakshatra', [{}])[0].get('name', 'N/A')
    
    # 🚫 Rahu Kaal (Safe Extraction)
    rahu_window = "N/A"
    if inner.get('rahu_kaal'):
        rk = inner.get('rahu_kaal')[0]
        rahu_window = f"{rk.get('start')[11:16]} - {rk.get('end')[11:16]}"

    # ✅ Abhijit Muhurat (The Golden Window)
    abhijit_window = "N/A"
    muhurtas = inner.get('muhurta', [])
    for m in muhurtas:
        if m.get('name') == 'Abhijit':
            abhijit_window = f"{m.get('start')[11:16]} - {m.get('end')[11:16]}"
            break

    # ✨ Shadbala Potency Filter
    planets_info = inner.get('planetary_strength', {}).get('planets', [])
    strength_map = {p['name']: p.get('shadbala', {}).get('ratio', 1.0) for p in planets_info}

    def calc_stars(planet_name):
        ratio = strength_map.get(planet_name, 1.0)
        score = 3
        if ratio > 1.2: score += 1
        elif ratio < 0.8: score -= 1
        return min(max(score, 1), 5)

    sector_map = {
        "☀️ PSU (Sun)": calc_stars("Sun"),
        "🌙 FMCG (Moon)": calc_stars("Moon"),
        "⚔️ Infra (Mars)": calc_stars("Mars"),
        "💻 IT/Tech (Mercury)": calc_stars("Mercury"),
        "🏦 Banking (Jupiter)": calc_stars("Jupiter"),
        "💎 Luxury/Auto (Venus)": calc_stars("Venus"),
        "⚒️ Metals (Saturn)": calc_stars("Saturn"),
        "🚀 NewTech (Rahu)": calc_stars("Rahu"),
        "💊 Pharma (Ketu)": calc_stars("Ketu")
    }
    heatmap = "\n".join([f"{k}: {'⭐' * v}" for k, v in sector_map.items()])

    report = (
        f"🏛️ *Vedic Institutional Quant* 🏛️\n"
        f"📅 {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"--------------------------\n"
        f"🌟 *ABHIJIT MUHURAT:* {abhijit_window}\n"
        f"🚫 *RAHU KAAL:* {rahu_window}\n"
        f"--------------------------\n"
        f"✨ {tithi} | ⭐ {nakshatra}\n"
        f"📊 RSI: {rsi} | VIX: {vix}\n"
        f"--------------------------\n"
        f"{heatmap}\n"
        f"--------------------------\n"
        f"🎯 *Conviction:* " + ("HIGH" if rsi < 65 and sentiment > 0.55 else "MODERATE") +
        f"\n--------------------------\n"
        f"⚠️ *Not SEBI advice.*"
    )
    return report

def main():
    try:
        token = get_prokerala_token()
        rsi, vix, sentiment = get_market_intelligence()
        vedic = get_vedic_data(token)
        report = generate_ultimate_report(vedic, rsi, vix, sentiment)
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": report, "parse_mode": "Markdown"})
        print("Success: Final Institutional Report Sent.")
    except Exception as e:
        print(f"Error in Main Execution: {e}")

if __name__ == "__main__":
    main()
