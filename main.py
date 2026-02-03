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
        # 1. Technical Data (Nifty 50)
        nifty = yf.download("^NSEI", period="60d", interval="1d")
        vix = yf.download("^INDIAVIX", period="1d")
        
        # Calculate RSI using pandas_ta
        nifty['RSI'] = ta.rsi(nifty['Close'], length=14)
        latest_rsi = round(nifty['RSI'].iloc[-1], 2)
        latest_vix = round(vix['Close'].iloc[-1].item(), 2)
        
        # 2. Finnhub News Sentiment
        # Global sentiment proxy (Apple) often leads tech/market mood
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
    
    # Logic: Cross-Verification
    sentiment_label = "Bullish 🟢" if sentiment > 0.6 else "Bearish 🔴" if sentiment < 0.4 else "Neutral ⚖️"
    conviction = "HIGH" if (rsi < 65 and sentiment > 0.55) else "MODERATE"
    if vix > 22: conviction = "LOW (Extreme Volatility)"

    # Sector Strength (Vedic + Sentiment Filter)
    # Using a 1-5 star system
    it_stars = 3
    if sentiment > 0.6: it_stars += 1
    if nakshatra in ["Revati", "Jyeshtha"]: it_stars += 1

    report = (
        f"🏛️ *Vedic Institutional Quant* 🏛️\n"
        f"📅 {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"✨ {tithi} | ⭐ {nakshatra}\n"
        f"--------------------------\n"
        f"📊 *Pulse:* RSI {rsi} | VIX {vix}\n"
        f"📰 *Sentiment:* {sentiment_label} ({int(sentiment*100)}%)\n"
        f"--------------------------\n"
        f"💻 IT (Mercury): {'⭐'*it_stars}\n"
        f"🏦 Banking (Jupiter): {'⭐'*4}\n"
        f"💊 Pharma (Ketu): {'⭐'*3}\n"
        f"--------------------------\n"
        f"🎯 *Conviction Score:* {conviction}\n"
        f"💡 *Astro-Tip:* " + ("Technical & Sentiment alignment confirmed." if conviction == "HIGH" else "Wait for technical cooling.") +
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
        print("Success: Final Institutional Report Sent.")
    except Exception as e:
        print(f"Deployment Error: {e}")

if __name__ == "__main__":
    main()
