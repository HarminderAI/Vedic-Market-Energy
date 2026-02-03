import os, json, datetime, time
import requests
import pytz
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import gspread
from google.oauth2.service_account import Credentials

# Ops & modules
from keep_alive import keep_alive
from news_logic import fetch_market_news, format_news_block

# ==========================================================
# CONFIG
# ==========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

SAFE_RSI = 50.0
SAFE_VIX = 15.0
SUCCESS_THRESHOLD = 0.20  # %

ANALYTICS_LOOKBACK = 100
VOLUME_SPIKE_MULTIPLIER = 2.0

IST = pytz.timezone("Asia/Kolkata")

SECTORS = {
    "PSU": "^CNXPSUBANK",
    "Infra": "^CNXINFRA",
    "IT": "^CNXIT",
    "BANK": "^NSEBANK",
    "FMCG": "^CNXFMCG",
    "AUTO": "^CNXAUTO",
    "METAL": "^CNXMETAL",
    "PHARMA": "^CNXPHARMA"
}

# ==========================================================
# GOOGLE SHEETS
# ==========================================================

def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_JSON, scopes=scopes)
    return gspread.authorize(creds)

gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)
state_ws = sheet.worksheet("state")
history_ws = sheet.worksheet("history")

# ==========================================================
# TELEGRAM
# ==========================================================

def send_msg(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except:
        pass

# ==========================================================
# TIME FILTER — VEDIC WINDOW
# ==========================================================

def is_vedic_trade_window():
    """
    Allowed trading window:
    09:30 AM – 03:00 PM IST
    """
    now = datetime.datetime.now(IST).time()
    return datetime.time(9, 30) <= now <= datetime.time(15, 0)

# ==========================================================
# SAFE MARKET DOWNLOAD
# ==========================================================

def safe_download(ticker, days=None, interval=None):
    try:
        df = yf.download(
            ticker,
            period=f"{days}d" if days else None,
            interval=interval,
            progress=False
        )
        if df is None or df.empty:
            return None
        df.columns = df.columns.get_level_values(0)
        return df
    except:
        return None

# ==========================================================
# TREND HEALTH (EMA20 STRETCH)
# ==========================================================

def trend_health(nifty_df):
    ema20 = ta.ema(nifty_df["Close"], 20).iloc[-1]
    close = nifty_df["Close"].iloc[-1]
    stretch = ((close - ema20) / ema20) * 100

    if abs(stretch) > 4:
        return "OVERSTRETCHED", round(stretch, 2), -20
    elif abs(stretch) > 2:
        return "EXTENDED", round(stretch, 2), -10
    else:
        return "HEALTHY", round(stretch, 2), 0

# ==========================================================
# MARKET REGIME
# ==========================================================

def market_regime_from_df(nifty_df):
    try:
        adx = ta.adx(
            nifty_df["High"], nifty_df["Low"], nifty_df["Close"]
        )["ADX_14"].iloc[-1]

        atr = ta.atr(
            nifty_df["High"], nifty_df["Low"], nifty_df["Close"]
        ).iloc[-1]

        vol = nifty_df["Close"].pct_change().std()

        if adx > 25 and atr > vol:
            return "CONFIRMED_TREND", 100
        if atr > vol * 1.5:
            return "VOLATILE", 40
        return "RANGE", 35
    except:
        return "UNKNOWN", 30

# ==========================================================
# SECTOR ROTATION + BREADTH
# ==========================================================

def sector_rotation_from_df(nifty_df):
    out = {}
    breadth = 0

    nifty_20d_ago = nifty_df["Close"].iloc[-20]
    nifty_ret = (nifty_df["Close"].iloc[-1] / nifty_20d_ago - 1) * 100

    for k, t in SECTORS.items():
        df = safe_download(t, 20)
        if df is None:
            out[k] = 0.0
            continue

        sec_ret = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        alpha = round(sec_ret - nifty_ret, 2)
        out[k] = alpha

        if alpha > 0:
            breadth += 1

    return out, breadth

# ==========================================================
# INTRADAY VOLUME SPIKE
# ==========================================================

def detect_volume_spike():
    if not is_vedic_trade_window():
        return

    df = safe_download("^NSEI", interval="15m", days=2)
    if df is None or len(df) < 12:
        return

    current_vol = df["Volume"].iloc[-1]
    avg_vol = df["Volume"].iloc[-11:-1].mean()

    if current_vol > avg_vol * VOLUME_SPIKE_MULTIPLIER:
        send_msg("🚨 Institutional Footprint Detected!\n15-min Volume Spike on NIFTY")

# ==========================================================
# TRADE SETUP ENGINE
# ==========================================================

def entry_engine(nifty_df, regime):
    atr = ta.atr(
        nifty_df["High"], nifty_df["Low"], nifty_df["Close"]
    ).iloc[-1]

    price = nifty_df["Close"].iloc[-1]

    if regime == "VOLATILE":
        sl = atr * 2.0
    elif regime == "RANGE":
        sl = atr * 1.2
    else:
        sl = atr * 1.5

    tp = sl * 2

    return {
        "Entry": round(price, 2),
        "SL": round(price - sl, 2),
        "TP": round(price + tp, 2)
    }

# ==========================================================
# MAIN (DAILY REPORT)
# ==========================================================

def main():
    nifty = safe_download("^NSEI", 80)
    vix = safe_download("^INDIAVIX", 10)

    if nifty is None:
        send_msg("❌ Market data unavailable.")
        return

    rsi = ta.rsi(nifty["Close"], 14).iloc[-1]
    regime, score_cap = market_regime_from_df(nifty)

    trend_state, stretch, trend_penalty = trend_health(nifty)
    sectors, breadth = sector_rotation_from_df(nifty)

    sentiment, subjectivity, headlines = fetch_market_news()
    news_block = format_news_block(sentiment, subjectivity, headlines)

    score = (
        (20 if rsi > 55 else 10) +
        (10 if breadth >= 5 else 0) +
        (10 if sentiment > 0 else 5) +
        trend_penalty
    )

    score = min(score, score_cap)
    bias = "BULLISH" if score >= 65 else "NEUTRAL"

    setup = entry_engine(nifty, regime)

    sector_text = "\n".join([f"• {k}: {v:+.2f}%" for k, v in sectors.items()])

    send_msg(
        "🏛️ Institutional Market Report\n"
        f"RSI: {round(rsi,2)}\n"
        f"Regime: {regime}\n"
        f"Trend Health: {trend_state} ({stretch:+.2f}%)\n"
        f"Breadth: {breadth}/8\n\n"
        f"{news_block}\n\n"
        f"Sector Rotation:\n{sector_text}\n\n"
        f"Trade Setup (If Any):\n"
        f"Entry: {setup['Entry']}\n"
        f"SL: {setup['SL']}\n"
        f"TP: {setup['TP']}\n\n"
        f"Score: {score}/100\n"
        f"Bias: {bias}\n\n"
        "⚠️ Not SEBI Advice"
    )

# ==========================================================
# BOOTSTRAP (RENDER SAFE)
# ==========================================================

if __name__ == "__main__":
    keep_alive(lambda: None)

    print("🚀 Morning Report Running...")
    main()

    while True:
        detect_volume_spike()
        time.sleep(900)  # 15 minutes
