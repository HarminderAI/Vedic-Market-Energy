from keep_alive import keep_alive
import os
import requests
import datetime
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import finnhub
import csv
from textblob import TextBlob

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
CLIENT_ID = os.environ.get('PROKERALA_CLIENT_ID')
CLIENT_SECRET = os.environ.get('PROKERALA_CLIENT_SECRET')
FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY')
GNEWS_API_KEY = os.environ.get('GNEWS_API_KEY')
AYANAMSA_MODE = 1 

NSE_SECTOR_MAP = {
    "☀️ PSU": "^CNXPSUBANK", "🌙 FMCG": "^CNXFMCG", "⚔️ Infra": "^CNXINFRA",
    "💻 IT": "^CNXIT", "🏦 Banking": "^NSEBANK", "💎 Auto": "^CNXAUTO",
    "⚒️ Metals": "^CNXMETAL", "🚀 Tech": "^CNXIT", "💊 Pharma": "^CNXPHARMA"
}

finnhub_client = finnhub.Client(api_key=FINNHUB_KEY)

# --- NEWS INTELLIGENCE ---
def fetch_market_news(query="Indian Stock Market"):
    url = f"https://gnews.io/api/v4/search?q={query}&lang=en&country=in&max=3&apikey={GNEWS_API_KEY}"
    try:
        response = requests.get(url)
        articles = response.json().get('articles', [])
        sentiment_score = 0
        headlines = []
        for art in articles:
            sentiment_score += TextBlob(art['title']).sentiment.polarity
            headlines.append(art['title'])
        avg_sentiment = sentiment_score / len(articles) if articles else 0
        return headlines, round(avg_sentiment, 2)
    except:
        return [], 0

# --- CORE UTILITIES ---
def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try: requests.post(url, data=payload)
    except: pass

def get_prokerala_token():
    url = "https://api.prokerala.com/token"
    data = {'grant_type': 'client_credentials', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}
    return requests.post(url, data=data).json().get('access_token')

def calculate_eod_performance():
    try:
        tickers = list(NSE_SECTOR_MAP.values())
        data = yf.download(tickers, period="2d", interval="1d", progress=False)
        eod_msg = "📊 *EOD Market Verification* 📊\n"
        for sector_name, ticker in NSE_SECTOR_MAP.items():
            if ticker in data['Close']:
                prices = data['Close'][ticker]
                pct = ((prices.iloc[-1] - prices.iloc[-2]) / prices.iloc[-2]) * 100
                eod_msg += f"{'📈' if pct > 0 else '📉'} {sector_name}: {pct:+.2f}%\n"
        return eod_msg
    except Exception as e: return f"EOD Error: {e}"

# Start heartbeat with EOD callback
keep_alive(lambda: send_telegram_msg(calculate_eod_performance()))

# --- REPORT GENERATION ---
def generate_ultimate_report(vedic, rsi, vix, news_data):
    headlines, news_sentiment = news_data
    if not vedic or 'data' not in vedic:
        return "❌ **Vedic Data Error:** API response empty."

    inner = vedic.get('data', {})
    tithi = inner.get('tithi', [{}])[0].get('name', 'N/A')
    nakshatra = inner.get('nakshatra', [{}])[0].get('name', 'N/A')
    
    rahu_data = inner.get('rahu_kaal', [])
    rahu_window = f"{rahu_data[0]['start'][11:16]} - {rahu_data[0]['end'][11:16]}" if rahu_data else "N/A"
    
    abhijit_window = "N/A"
    for m in inner.get('muhurta', []):
        if m.get('name') == 'Abhijit':
            abhijit_window = f"{m.get('start')[11:16]} - {m.get('end')[11:16]}"
            break

    planets_info = inner.get('planetary_strength', {}).get('planets', [])
    strength_map = {p['name']: p.get('shadbala', {}).get('ratio', 1.0) for p in planets_info}

    def calc_stars(planet_name):
        ratio = strength_map.get(planet_name, 1.0)
        stars = 3
        if ratio >= 1.3: stars = 5
        elif ratio >= 1.1: stars = 4
        elif ratio <= 0.7: stars = 1
        elif ratio <= 0.9: stars = 2
        
        # News Sentiment Adjustment
        if news_sentiment > 0.15: stars = min(stars + 1, 5)
        elif news_sentiment < -0.15: stars = max(stars - 1, 1)
        return stars

    sector_map = {k: calc_stars(v) for k, v in {
        "☀️ PSU": "Sun", "🌙 FMCG": "Moon", "⚔️ Infra": "Mars", 
        "💻 IT": "Mercury", "🏦 Banking": "Jupiter", "💎 Auto": "Venus",
        "⚒️ Metals": "Saturn", "🚀 Tech": "Rahu", "💊 Pharma": "Ketu"
    }.items()}
    
    heatmap = "\n".join([f"{k}: {'⭐' * v}" for k, v in sector_map.items()])
    news_bullet = "\n".join([f"• {h[:55]}..." for h in headlines])

    report = (
        f"🏛️ *Vedic Institutional Quant* 🏛️\n"
        f"📅 {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"--------------------------\n"
        f"🌟 *ABHIJIT:* {abhijit_window} | 🚫 *RAHU:* {rahu_window}\n"
        f"--------------------------\n"
        f"✨ {tithi} | ⭐ {nakshatra}\n"
        f"📊 RSI: {rsi} | VIX: {vix}\n"
        f"--------------------------\n"
        f"📰 *News Sentiment:* {news_sentiment:+.2f}\n"
        f"{news_bullet}\n"
        f"--------------------------\n"
        f"{heatmap}\n"
        f"--------------------------\n"
        f"🎯 *Final Conviction:* {'HIGH' if news_sentiment > 0.1 else 'MODERATE'}\n"
        f"--------------------------\n"
        f"⚠️ *Not SEBI advice.*"
    )
    return report

def main():
    try:
        token = get_prokerala_token()
        news_data = fetch_market_news()
        
        # Tech Intelligence
        nifty = yf.download("^NSEI", period="5d", progress=False)
        rsi = round(ta.rsi(nifty['Close'], length=14).iloc[-1], 2)
        vix = round(yf.download("^INDIAVIX", period="1d", progress=False)['Close'].iloc[-1], 2)
        
        # Vedic Data
        params = {
            'datetime': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+05:30'),
            'coordinates': '23.1765,75.7885', 'ayanamsa': AYANAMSA_MODE,
            'la-dimension': 'planet-position,rahu-kaal,ashtakavarga,planetary-strength,muhurta'
        }
        vedic = requests.get("https://api.prokerala.com/v2/astrology/panchang", 
                             params=params, headers={'Authorization': f'Bearer {token}'}).json()
        
        report = generate_ultimate_report(vedic, rsi, vix, news_data)
        send_telegram_msg(report)
        print("Report sent successfully.")

    except Exception as e:
        send_telegram_msg(f"❌ **Bot Error:** {str(e)}")

if __name__ == "__main__":
    main()
