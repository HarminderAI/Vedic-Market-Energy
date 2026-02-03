from keep_alive import keep_alive
import os
import requests
import datetime
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import finnhub
import csv

# Start the 'heartbeat' server
# Note: Ensure your keep_alive.py supports the callback for EOD
keep_alive(lambda: send_telegram_msg(calculate_eod_performance()))

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
CLIENT_ID = os.environ.get('PROKERALA_CLIENT_ID')
CLIENT_SECRET = os.environ.get('PROKERALA_CLIENT_SECRET')
FINNHUB_KEY = os.environ.get('FINNHUB_API_KEY')
AYANAMSA_MODE = 1 

# --- EOD SECTOR MAPPING ---
NSE_SECTOR_MAP = {
    "☀️ PSU": "^CNXPSUBANK",    
    "🌙 FMCG": "^CNXFMCG",       
    "⚔️ Infra": "^CNXINFRA",     
    "💻 IT": "^CNXIT",         
    "🏦 Banking": "^NSEBANK", 
    "💎 Auto": "^CNXAUTO",       
    "⚒️ Metals": "^CNXMETAL",   
    "🚀 Tech": "^CNXIT",         
    "💊 Pharma": "^CNXPHARMA"    
}

finnhub_client = finnhub.Client(api_key=FINNHUB_KEY)

# --- CORE UTILITIES ---

def send_telegram_msg(text):
    """Helper function to send messages to Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram Post Error: {e}")

def get_prokerala_token():
    url = "https://api.prokerala.com/token"
    data = {'grant_type': 'client_credentials', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}
    response = requests.post(url, data=data)
    return response.json().get('access_token')

def log_prediction(sector_map):
    """Saves 4 and 5 star predictions for weekend review."""
    file_path = 'backtest_log.csv'
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    top_picks = [s for s, stars in sector_map.items() if stars >= 4]
    
    file_exists = os.path.isfile(file_path)
    with open(file_path, mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Top_Sectors'])
        writer.writerow([today, ",".join(top_picks)])

# --- MARKET & VEDIC INTELLIGENCE ---

def get_market_intelligence():
    try:
        nifty = yf.download("^NSEI", period="60d", interval="1d", progress=False)
        vix = yf.download("^INDIAVIX", period="5d", interval="1d", progress=False)
        nifty['RSI'] = ta.rsi(nifty['Close'], length=14)
        latest_rsi = float(nifty['RSI'].iloc[-1]) if not nifty['RSI'].empty else 50.0
        latest_vix = float(vix['Close'].iloc[-1]) if not vix.empty else 15.0
        sentiment_data = finnhub_client.news_sentiment('AAPL')
        bullish_pct = sentiment_data.get('sentiment', {}).get('bullishPercent', 0.5)
        return round(latest_rsi, 2), round(latest_vix, 2), bullish_pct
    except:
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

# --- EOD LOGIC ---

def calculate_eod_performance():
    """Fetches daily % change for verification."""
    try:
        tickers = list(NSE_SECTOR_MAP.values())
        data = yf.download(tickers, period="2d", interval="1d", progress=False)
        eod_msg = "📊 *EOD Market Verification* 📊\n"
        eod_msg += f"📅 {datetime.datetime.now().strftime('%d %b %Y')}\n\n"
        
        for sector_name, ticker in NSE_SECTOR_MAP.items():
            if ticker in data['Close']:
                prices = data['Close'][ticker]
                if len(prices) >= 2:
                    pct = ((prices.iloc[-1] - prices.iloc[-2]) / prices.iloc[-2]) * 100
                    emoji = "📈" if pct > 0 else "📉"
                    eod_msg += f"{emoji} {sector_name}: {pct:+.2f}%\n"
        return eod_msg
    except Exception as e:
        return f"EOD Verification Error: {e}"

# --- REPORT GENERATION ---

def generate_ultimate_report(vedic, rsi, vix, sentiment):
    # Deep Defensive Check 1: Ensure vedic data exists
    if not vedic or 'data' not in vedic:
        return "❌ **Vedic Data Error:** API returned an empty response. Check Prokerala Credentials/Quota."

    inner = vedic.get('data', {})
    
    # Deep Defensive Check 2: Safe Tithi/Nakshatra extraction
    tithi_data = inner.get('tithi', [])
    tithi = tithi_data[0].get('name', 'N/A') if tithi_data else "N/A"
    
    nakshatra_data = inner.get('nakshatra', [])
    nakshatra = nakshatra_data[0].get('name', 'N/A') if nakshatra_data else "N/A"
    
    # Safe Rahu/Abhijit Slicing
    rahu_data = inner.get('rahu_kaal', [])
    rahu_window = "N/A"
    if rahu_data and 'start' in rahu_data[0]:
        try:
            rahu_window = f"{rahu_data[0]['start'][11:16]} - {rahu_data[0]['end'][11:16]}"
        except: pass

    abhijit_window = "N/A"
    for m in inner.get('muhurta', []):
        if m.get('name') == 'Abhijit':
            abhijit_window = f"{m.get('start')[11:16]} - {m.get('end')[11:16]}"
            break

    # 1. Fetch Shadbala Potency
    planets_info = inner.get('planetary_strength', {}).get('planets', [])
    strength_map = {p['name']: p.get('shadbala', {}).get('ratio', 1.0) for p in planets_info}

    # 2. NEW DYNAMIC STAR LOGIC
    def calc_stars(planet_name):
        ratio = strength_map.get(planet_name, 1.0)
        
        # More sensitive 5-point scale
        if ratio >= 1.3: return 5
        if ratio >= 1.1: return 4
        if ratio <= 0.7: return 1
        if ratio <= 0.9: return 2
        return 3 # Base for ratio 0.91 to 1.09

    sector_map = {
        "☀️ PSU": calc_stars("Sun"), "🌙 FMCG": calc_stars("Moon"),
        "⚔️ Infra": calc_stars("Mars"), "💻 IT": calc_stars("Mercury"),
        "🏦 Banking": calc_stars("Jupiter"), "💎 Auto": calc_stars("Venus"),
        "⚒️ Metals": calc_stars("Saturn"), "🚀 Tech": calc_stars("Rahu"),
        "💊 Pharma": calc_stars("Ketu")
    }
    
    log_prediction(sector_map)
    heatmap = "\n".join([f"{k}: {'⭐' * v}" for k, v in sector_map.items()])

    report = (
        f"🏛️ *Vedic Institutional Quant* 🏛️\n"
        f"📅 {datetime.datetime.now().strftime('%d %b %Y')}\n"
        f"--------------------------\n"
        f"🌟 *ABHIJIT:* {abhijit_window} | 🚫 *RAHU:* {rahu_window}\n"
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

# --- EXECUTION ---

def main():
    try:
        # 1. MORNING REPORT FLOW
        token = get_prokerala_token()
        rsi, vix, sentiment = get_market_intelligence()
        vedic = get_vedic_data(token)
        report = generate_ultimate_report(vedic, rsi, vix, sentiment)
        
        send_telegram_msg(report)
        print("Morning report sent successfully.")

    except Exception as e:
        error_message = f"❌ **Bot Error:** {str(e)}"
        print(error_message)
        try:
            send_telegram_msg(error_message)
        except:
            pass

if __name__ == "__main__":
    main()
