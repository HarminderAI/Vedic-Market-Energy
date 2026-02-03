# ==========================================================
# 🏛️ INSTITUTIONAL MARKET BOT — FINAL (PRODUCTION)
# ==========================================================

import os, json, datetime
import requests
import time
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

ANALYTICS_LOOKBACK = 100  # last N rows only

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
# METRICS FROM PRELOADED NIFTY + VIX
# ==========================================================

def market_metrics_from_df(nifty_df, vix_df):
    try:
        if nifty_df is None or len(nifty_df) < 20:
            return SAFE_RSI, SAFE_VIX, False, None

        rsi_series = ta.rsi(nifty_df["Close"], 14)
        rsi = SAFE_RSI if rsi_series is None or pd.isna(rsi_series.iloc[-1]) else round(float(rsi_series.iloc[-1]), 2)

        vol_avg = nifty_df["Volume"].rolling(10).mean()
        vol_ok = not pd.isna(vol_avg.iloc[-1]) and nifty_df["Volume"].iloc[-1] > vol_avg.iloc[-1]

        close = float(nifty_df["Close"].iloc[-1])

        if vix_df is None or vix_df.empty:
            vix = SAFE_VIX
        else:
            vix = round(float(vix_df["Close"].iloc[-1]), 2)

        return rsi, vix, vol_ok, close
    except:
        return SAFE_RSI, SAFE_VIX, False, None

# ==========================================================
# MARKET REGIME
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
# SECTOR ROTATION (20D APPLES-TO-APPLES)
# ==========================================================

def sector_rotation_from_df(nifty_df):
    out = {}
    try:
        nifty_20d_ago = nifty_df["Close"].iloc[-20]
        nifty_ret = (nifty_df["Close"].iloc[-1] / nifty_20d_ago - 1) * 100
    except:
        nifty_ret = 0.0

    for k, t in SECTORS.items():
        df = safe_download(t, 20)
        if df is None or len(df) < 2:
            out[k] = 0.0
            continue
        sec_ret = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
        out[k] = round(sec_ret - nifty_ret, 2)

    return out

# ==========================================================
# ACCURACY ANALYTICS (OPTIMIZED)
# ==========================================================

def compute_accuracy(limit=ANALYTICS_LOOKBACK):
    try:
        rows = history_ws.get_all_values()
        if len(rows) <= 1:
            return "📈 Performance Snapshot\nNo data yet."

        headers = rows[0]
        data = rows[-limit:]
        df = pd.DataFrame(data, columns=headers)

        df = df[df["result"].isin(["CORRECT", "INCORRECT"])]
        if df.empty:
            return "📈 Performance Snapshot\nNo valid outcomes yet."

        lines = ["📈 Performance Snapshot"]

        overall = round((df["result"] == "CORRECT").mean() * 100, 1)
        lines.append(f"Overall Accuracy: {overall}%\n")

        lines.append("📌 Win-Rate by Bias")
        for bias in ["BULLISH", "NEUTRAL"]:
            sub = df[df["bias"] == bias]
            if sub.empty:
                lines.append(f"{bias}: No data")
                continue
            acc = round((sub["result"] == "CORRECT").mean() * 100, 1)
            lines.append(f"{bias}: {acc}% (Trades: {len(sub)})")

        lines.append("\n📌 Win-Rate by Regime")
        for regime in ["TRENDING", "RANGE", "VOLATILE", "UNKNOWN"]:
            sub = df[df["regime"] == regime]
            if sub.empty:
                continue
            acc = round((sub["result"] == "CORRECT").mean() * 100, 1)
            lines.append(f"{regime}: {acc}% (Trades: {len(sub)})")

        lines.append("\n📌 Win-Rate by Score")
        for low, high in [(60,64),(65,69),(70,74),(75,100)]:
            sub = df[(df["score"].astype(float) >= low) & (df["score"].astype(float) <= high)]
            if sub.empty:
                continue
            acc = round((sub["result"] == "CORRECT").mean() * 100, 1)
            lines.append(f"{low}-{high}: {acc}% (Trades: {len(sub)})")

        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        recent = df.sort_values("date").tail(30)
        if len(recent) >= 5:
            roll = round((recent["result"] == "CORRECT").mean() * 100, 1)
            lines.append(f"\n📉 Rolling 30-Day Accuracy: {roll}%")

        return "\n".join(lines)

    except Exception as e:
        return f"📈 Performance Snapshot\nError computing accuracy: {e}"

def main():
    # ----------------------------
    # Load previous state (if any)
    # ----------------------------
    rows = state_ws.get_all_records()
    prev = None
    if rows:
        try:
            prev = json.loads(rows[0]["value"])
        except:
            prev = None

    # ----------------------------
    # Download market data ONCE
    # ----------------------------
    nifty = safe_download("^NSEI", 80)
    vix = safe_download("^INDIAVIX", 10)

    if nifty is None:
        send_msg("❌ Market data unavailable. Morning report skipped.")
        return

    # ----------------------------
    # Compute metrics
    # ----------------------------
    rsi, vix_val, vol_ok, close = market_metrics_from_df(nifty, vix)
    regime = market_regime_from_df(nifty)
    sectors = sector_rotation_from_df(nifty)

    news_sentiment, news_subjectivity, headlines = fetch_market_news()
    news_block = format_news_block(news_sentiment, news_subjectivity, headlines)

    # ----------------------------
    # Score & Bias
    # ----------------------------
    score = (
        (20 if rsi > 55 else 10) +
        (20 if vol_ok else 10) +
        (10 if news_sentiment > 0 else 5)
    )

    bias = "BULLISH" if score >= 65 else "NEUTRAL"

    # ----------------------------
    # Validate Yesterday (EOD)
    # ----------------------------
    if prev:
        try:
            delta = ((close - prev["nifty_close"]) / prev["nifty_close"]) * 100
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
        except:
            pass

    # ----------------------------
    # Save Today State
    # ----------------------------
    state_ws.clear()
    state_ws.append_row([
        "yesterday",
        json.dumps({
            "date": str(datetime.date.today()),
            "bias": bias,
            "score": score,
            "regime": regime,
            "nifty_close": close
        })
    ])

    # ----------------------------
    # Telegram Report
    # ----------------------------
    sector_text = "\n".join([f"• {k}: {v:+.2f}%" for k, v in sectors.items()])

    send_msg(
        "🏛️ Institutional Market Report\n"
        f"RSI: {rsi}\n"
        f"VIX: {vix_val}\n"
        f"Volume Confirmed: {vol_ok}\n"
        f"Regime: {regime}\n\n"
        f"{news_block}\n\n"
        f"Sector Rotation:\n{sector_text}\n\n"
        f"Score: {score}/100\n"
        f"Bias: {bias}\n\n"
        "⚠️ Not SEBI Advice"
    )

if __name__ == "__main__":
    # 1. Start the web server in a background thread
    # This binds to Render's $PORT and prevents restarts
    keep_alive(lambda: send_msg(compute_accuracy()))

    # 2. Run the morning report exactly once
    print("🚀 Running morning report...")
    main()

    # 3. Prevent Render from restarting the service
    # This keeps the process alive indefinitely
    while True:
        time.sleep(3600)  # sleep in 1-hour intervals
