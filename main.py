from keep_alive import keep_alive
import os
import requests
import datetime
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import finnhub

# Start the 'heartbeat' server for 24/7 uptime
keep_alive()

# --- CONFIGURATION (From Environment Variables) ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
CLIENT_ID = os.environ.get('PROKERALA_CLIENT_ID')
CLIENT_SECRET = os.environ.get('PROKERALA_CLIENT_SECRET')
FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY')

# Initialize Finnhub Client
finnhub_client = finnhub.Client(api_key=FINNHUB_KEY)

def get_prokerala_token():
    url = "https://api.prokerala.com/token"
    data = {'grant_type': 'client_credentials', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}
    response = requests.post(url, data=data)
    return response.json().get('access_token')

def get_market_intelligence():
    """Fetches Technicals (RSI/VIX) and Sentiment (Finnhub)."""
    try:
        nifty = yf.download("^NSEI", period="60d", interval="1d")
        vix = yf.download("^INDIAVIX", period="1d")
        
        nifty['RSI'] = ta.rsi(nifty['Close'], length=14)
        latest_rsi = round(nifty['RSI'].iloc[-1], 2)
        latest_vix = round(vix['Close'].iloc[-1].item(), 2)
        
        sentiment_data = finnhub_client.news_sentiment('AAPL')
        bullish_pct = sentiment_data.get('sentiment', {}).get('bullishPercent', 0.5)
        
        return latest_rsi, latest_vix, bullish_pct
    except Exception as e:
        print(f"Data Error: {e}")
        return 50.0, 15.0, 0.5

def get_vedic_data(token):
    """Fetches Institutional Vedic Dimensions."""
    url = "https://api.prokerala.com/v2/astrology/panchang"
    params = {
        'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+05:30'),
        'coordinates': '23.1765,75.7885',
        'ayanamsa': 1,
        'la-dimension': 'planet-position,rahu-kaal,ashtakavarga,planetary-strength'
    }
    headers = {'Authorization': f'Bearer {token}'}
    return requests.get(url, params=params, headers=headers).json()

def generate_ultimate_report(vedic, rsi, vix, sentiment):
    inner = vedic.get('data', {})
    tithi = inner.get('tithi', [{}])[0].get('name', 'N/A')
    nakshatra = inner.get('nakshatra', [{}])[0].get('name', 'N/A')
    
    # 1. Rahu Kaal Timer Extraction
    rahu_info = inner.get('rahu_kaal', [{}])[0]
    rahu_start = datetime.datetime.fromisoformat(rahu_info.get('start')).strftime('%I:%M %p')
    rahu_end = datetime.datetime.fromisoformat(rahu_info.get('end')).strftime('%I:%M %p')

    # 2. Market Sentiment & Conviction Logic
    sentiment_label = "Bullish 🟢" if sentiment > 0.6 else "Bearish 🔴" if sentiment < 0.4 else "Neutral ⚖️"
    conviction = "HIGH" if (rsi < 65 and sentiment > 0.55) else "MODERATE"
    if vix > 22: conviction = "LOW (Extreme Volatility)"

    # 3. Shadbala (Planetary Strength) Logic
    planets_info = inner.get('planetary_strength', {}).get('planets', [])
    strength_map = {p['name']: p.get('shadbala', {}).get('ratio', 1.0) for p in planets_info}

    # Helper for 9-Sector Mapping with Shadbala weights
    def calc_stars(planet_name, base=3):
        ratio = strength_map.get(planet_name, 1.0)
        score = base
        if ratio > 1.2: score += 1
        elif ratio < 0.8: score -= 1
        if planet_name in ["Mercury", "Rahu"] and sentiment > 0.6: score += 1
        return min(max(score, 1), 5)

    sector_heatmap = {
        "☀️ PSU/Energy (Sun)": calc_stars("Sun"),
        "🌙 FMCG/Dairy (Moon)": calc_stars("Moon"),
        "⚔️ Real Estate (Mars)": calc_stars("Mars"),
        "💻 IT/Tech (Mercury)": calc_stars("Mercury"),
        "🏦 Banking/Fin (Jupiter)": calc_stars("Jupiter"),
        "💎 Luxury/Auto (Venus)": calc_stars("Venus"),
        "⚒️ Metals/Mining (Saturn)": calc_stars("Saturn"),
        "🚀 Aviation/NewTech (Rahu)": calc_stars("Rahu"),
        "💊 Pharma/Res (Ketu)": calc_stars("Ketu")
    }
    heatmap_text = "\n".join([f"{k}: {'⭐' * v}" for k, v in sector_heatmap.items()])

    # 4. Assembly of Final Report
    report = (
        f"🏛️ *Vedic Institutional Quant* 🏛️\n"
        f"📅 {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"✨ {tithi} | ⭐ {nakshatra}\n"
        f"--------------------------\n"
        f"🚫 *RAHU KAAL (No Trade):* {rahu_start} - {rahu_end}\n"
        f"--------------------------\n"
        f"📊 *Pulse:* RSI {rsi} | VIX {vix}\n"
        f"📰 *Sentiment:* {sentiment_label} ({int(sentiment*100)}%)\n"
        f"--------------------------\n"
        f"{heatmap_text}\n"
        f"--------------------------\n"
        f"🎯 *Conviction Score:* {conviction}\n"
        f"💡 *Tip:* Avoid fresh positions during Rahu Kaal. Focus on Shadbala-strong sectors today."
        f"\n--------------------------\n"
        f"⚠️ *Educational Study Only. Not SEBI advice.*"
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
        print("Success: 9-Planet Institutional Report with Rahu Kaal Sent.")
    except Exception as e:
        print(f"Deployment Error: {e}")

if __name__ == "__main__":
    main()
