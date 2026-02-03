from keep_alive import keep_alive
import os
import requests
import datetime
import yfinance as yf
import pandas as pd
import finnhub

# Start the 'heartbeat' server
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

def get_market_quant_data():
    """Fetches RSI, VIX, and News Sentiment."""
    try:
        # 1. Technical Metrics (Nifty 50)
        nifty = yf.download("^NSEI", period="60d", interval="1d")
        vix = yf.download("^INDIAVIX", period="1d")
        
        # Manual RSI Calculation
        delta = nifty['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        latest_rsi = round(rsi.iloc[-1].item(), 2)
        latest_vix = round(vix['Close'].iloc[-1].item(), 2)
        
        # 2. News Sentiment (Global Market Sentiment proxy)
        sentiment_data = finnhub_client.news_sentiment('AAPL') # Using Apple as a global tech sentiment proxy
        bullish_pct = sentiment_data.get('sentiment', {}).get('bullishPercent', 0.5)
        
        return latest_rsi, latest_vix, bullish_pct
    except Exception as e:
        print(f"Market Data Error: {e}")
        return 50.0, 15.0, 0.5

def get_vedic_data(token):
    url = "https://api.prokerala.com/v2/astrology/panchang"
    params = {
        'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+05:30'),
        'coordinates': '23.1765,75.7885', # Ujjain
        'ayanamsa': 1,
        'la-dimension': 'planet-position,rahu-kaal,hora'
    }
    headers = {'Authorization': f'Bearer {token}'}
    return requests.get(url, params=params, headers=headers).json()

def generate_final_report(vedic, rsi, vix, sentiment):
    inner = vedic.get('data', {})
    tithi = inner.get('tithi', [{}])[0].get('name', 'N/A')
    nakshatra = inner.get('nakshatra', [{}])[0].get('name', 'N/A')
    hora = inner.get('hora', [{}])[0].get('name', 'N/A')
    
    # Logic: Confirmation Filter
    sentiment_label = "Bullish 🟢" if sentiment > 0.6 else "Bearish 🔴" if sentiment < 0.4 else "Neutral ⚖️"
    
    # High Conviction logic
    conviction = "HIGH" if (rsi < 70 and sentiment > 0.5) else "MODERATE"
    if vix > 20: conviction = "LOW (High Volatility)"

    report = (
        f"🏛️ *Vedic Quant Institutional* 🏛️\n"
        f"📅 {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"✨ Tithi: {tithi} | ⭐ Nakshatra: {nakshatra}\n"
        f"⌛ Current Hora: {hora}\n"
        f"--------------------------\n"
        f"📊 *Market Pulse:* RSI: {rsi} | VIX: {vix}\n"
        f"📰 *News Sentiment:* {sentiment_label} ({int(sentiment*100)}%)\n"
        f"--------------------------\n"
        f"💻 IT (Mercury): {'⭐'* (3 if sentiment > 0.4 else 2)}\n"
        f"🏦 Banking (Jupiter): {'⭐'*4}\n"
        f"💊 Pharma (Ketu): {'⭐'*3}\n"
        f"--------------------------\n"
        f"🎯 *Conviction Score:* {conviction}\n"
        f"💡 *Tip:* " + ("Technical confirmation received." if conviction == "HIGH" else "Wait for RSI to cool down.") +
        f"\n--------------------------\n"
        f"⚠️ *Educational Study Only. Not SEBI advice.*"
    )
    return report

def main():
    try:
        token = get_prokerala_token()
        rsi, vix, sentiment = get_market_quant_data()
        vedic = get_vedic_data(token)
        report = generate_final_report(vedic, rsi, vix, sentiment)
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": report, "parse_mode": "Markdown"})
        print("Final Institutional Report Sent.")
    except Exception as e:
        print(f"Error in Main: {e}")

if __name__ == "__main__":
    main()
