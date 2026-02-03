# ==========================================================
# 🏛️ INSTITUTIONAL MARKET BOT — FINAL (RATE-LIMIT SAFE)
# ==========================================================

import os, json, datetime
import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from textblob import TextBlob
import gspread
from google.oauth2.service_account import Credentials

# ==========================================================
# CONFIG
# ==========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

SAFE_RSI = 50.0
SAFE_VIX = 15.0
SUCCESS_THRESHOLD = 0.20  # %

SECTORS = {
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
# SAFE MARKET DOWNLOAD
# ==========================================================

def safe_download(ticker, days):
    try:
        df = yf.download(ticker, period=f"{days}d", progress=False)
        if df is None or df.empty:
            return None
        df.columns = df.columns.get_level_values(0)
        return df
    except:
        return None

# ==========================================================
# NEWS SENTIMENT (CONTEXT ONLY)
# ==========================================================

def fetch_news_sentiment():
    try:
        url = (
            "https://gnews.io/api/v4/search"
            "?q=Nifty%20OR%20Indian%20Stock%20Market"
            "&lang=en&country=in&max=3"
            f"&apikey={GNEWS_API_KEY}"
        )
        arts = requests.get(url, timeout=10).json().get("articles", [])
        if not arts:
            return 0.0
        return round(sum(TextBlob(a["title"]).sentiment.polarity for a in arts) / len(arts), 2)
    except:
        return 0.0

# ==========================================================
# METRICS FROM PRELOADED NIFTY
# ==========================================================

def market_metrics_from_df(nifty_df):
    try:
        if nifty_df is None or len(nifty_df) < 20:
            return SAFE_RSI, False, None

        rsi_series = ta.rsi(nifty_df["Close"], 14)
        rsi = SAFE_RSI if rsi_series is None or pd.isna(rsi_series.iloc[-1]) else round(float(rsi_series.iloc[-1]), 2)

        vol_avg = nifty_df["Volume"].rolling(10).mean()
        vol_ok = not pd.isna(vol_avg.iloc[-1]) and nifty_df["Volume"].iloc[-1] > vol_avg.iloc[-1]

        close = float(nifty_df["Close"].iloc[-1])

        return rsi, vol_ok, close
    except:
        return SAFE_RSI, False, None

# ==========================================================
# MARKET REGIME FROM SAME DATA
# ==========================================================

def market_regime_from_df(nifty_df):
    try:
        adx = ta.adx(nifty_df["High"], nifty_df["Low"], nifty_df["Close"])["ADX_14"].iloc[-1]
        atr = ta.atr(nifty_df["High"], nifty_df["Low"], nifty_df["Close"]).iloc[-1]
        vol = nifty_df["Close"].pct_change().std()

        if adx > 20 and atr > vol:
            return "TRENDING"
        if atr > vol * 1.5:
            return "VOLATILE"
        return "RANGE"
    except:
        return "UNKNOWN"

# ==========================================================
# SECTOR ROTATION (RELATIVE STRENGTH)
# ==========================================================

def sector_rotation_from_df(nifty_df):
    out = {}
    try:
        nifty_ret = (nifty_df["Close"].iloc[-1] / nifty_df["Close"].iloc[0] - 1) * 100
    except:
        nifty_ret = 0.0

    for k, t in SECTORS.items():
        df = safe_download(t, 20)
        if df is None:
            out[k] = 0.0
            continue
        sec_ret = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        out[k] = round(sec_ret - nifty_ret, 2)

    return out

# ==========================================================
# STATE HANDLING (SHEETS)
# ==========================================================

def load_yesterday():
    rows = state_ws.get_all_records()
    if not rows:
        return None
    try:
        return json.loads(rows[0]["value"])
    except:
        return None

def save_today(snapshot):
    state_ws.clear()
    state_ws.append_row(["yesterday", json.dumps(snapshot)])

# ==========================================================
# VALIDATION
# ==========================================================

def validate_yesterday(prev, today_close):
    try:
        delta = ((today_close - prev["nifty_close"]) / prev["nifty_close"]) * 100
    except:
        return

    if delta >= SUCCESS_THRESHOLD:
        result = "CORRECT"
    elif delta <= -SUCCESS_THRESHOLD:
        result = "INCORRECT"
    else:
        result = "NO_EDGE"

    history_ws.append_row([
        prev["date"], prev["bias"], prev["score"],
        prev["regime"], round(delta, 2), result
    ])

    send_msg(
        f"📊 Yesterday Validation\n"
        f"Bias: {prev['bias']}\n"
        f"Move: {delta:+.2f}%\n"
        f"Result: {result}"
    )

# ==========================================================
# MAIN
# ==========================================================

def main():
    prev = load_yesterday()

    nifty = safe_download("^NSEI", 80)
    if nifty is None:
        return

    rsi, vol_ok, close = market_metrics_from_df(nifty)
    regime = market_regime_from_df(nifty)
    news = fetch_news_sentiment()
    sectors = sector_rotation_from_df(nifty)

    score = (
        (20 if rsi > 55 else 10) +
        (20 if vol_ok else 10) +
        (10 if news > 0 else 5)
    )

    bias = "BULLISH" if score >= 65 else "NEUTRAL"

    if prev:
        validate_yesterday(prev, close)

    snapshot = {
        "date": str(datetime.date.today()),
        "bias": bias,
        "score": score,
        "regime": regime,
        "nifty_close": close
    }

    save_today(snapshot)

    sector_text = "\n".join([f"• {k}: {v:+.2f}%" for k, v in sectors.items()])

    send_msg(
        "🏛️ Institutional Market Report\n"
        f"RSI: {rsi}\n"
        f"Volume Confirmed: {vol_ok}\n"
        f"Regime: {regime}\n\n"
        f"Sector Rotation:\n{sector_text}\n\n"
        f"Score: {score}/100\n"
        f"Bias: {bias}\n\n"
        "⚠️ Not SEBI Advice"
    )

if __name__ == "__main__":
    main()
