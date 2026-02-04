# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL (2026 PRODUCTION)
# ==========================================================

import os, json, time, datetime
import requests, pytz
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import gspread
from google.oauth2.service_account import Credentials
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from keep_alive import keep_alive
from news_logic import fetch_market_news, format_news_block, analyze_news_sentiment

# ==========================================================
# TIMEZONE
# ==========================================================
IST = pytz.timezone("Asia/Kolkata")

def ist_today():
    return str(datetime.datetime.now(IST).date())

# ==========================================================
# CONFIG
# ==========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

BASE_EXPOSURE_CAP = 0.90
SECTOR_CAP = 0.50
EXIT_SCORE_DROP = 15
MAX_WORKERS = 5

# ==========================================================
# TELEGRAM
# ==========================================================
def send_msg(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)

# ==========================================================
# GOOGLE SHEETS (SAFE REFRESH)
# ==========================================================
def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_JSON, scopes=scopes)
    return gspread.authorize(creds)

gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

def safe_sheet(name, headers):
    global gc, sheet
    try:
        return sheet.worksheet(name)
    except:
        gc = sheet_client()
        sheet = gc.open_by_key(GOOGLE_SHEET_ID)
        try:
            return sheet.worksheet(name)
        except:
            ws = sheet.add_worksheet(name, rows=300, cols=len(headers))
            ws.append_row(headers)
            return ws

state_ws   = safe_sheet("state",   ["key", "value"])
stocks_ws  = safe_sheet("stocks",  ["symbol","score","bucket","size","health","sector"])
history_ws = safe_sheet("history", ["date","symbol","event","detail"])

# ==========================================================
# MARKET DATA
# ==========================================================
def safe_download(ticker, days=80):
    try:
        df = yf.download(
            ticker,
            period=f"{days}d",
            auto_adjust=True,
            progress=False
        )
        if df is None or df.empty:
            return None
        if df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        return df.dropna()
    except:
        return None

def batch_download(tickers):
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(safe_download, t): t for t in tickers}
        for f in as_completed(futures):
            sym = futures[f]
            df = f.result()
            if df is not None:
                out[sym] = df
    return out

# ==========================================================
# INDICATORS
# ==========================================================
def trend_health(df):
    close = df["Close"]
    if len(close) < 25:
        return "UNKNOWN"
    ema = ta.ema(close, 20)
    if ema is None or ema.dropna().empty:
        return "UNKNOWN"
    stretch = (close.iloc[-1] - ema.dropna().iloc[-1]) / ema.dropna().iloc[-1] * 100
    if stretch > 4: return "OVERSTRETCHED"
    if stretch < -2: return "DEEP_PULLBACK"
    return "HEALTHY"

def score_stock(df):
    rsi = ta.rsi(df["Close"], 14)
    rsi = rsi.dropna().iloc[-1] if rsi is not None and not rsi.dropna().empty else 50
    health = trend_health(df)

    score = 30 if rsi > 60 else 15 if rsi > 50 else 5
    if health == "OVERSTRETCHED": score -= 20
    elif health == "HEALTHY": score += 10

    return max(0, min(100, score)), health

def assign_bucket(score, strong_buy_allowed):
    if score >= 80 and strong_buy_allowed: return "STRONG_BUY"
    if score >= 65: return "BUY"
    if score >= 50: return "WATCHLIST"
    return "AVOID"

def position_size(bucket):
    return {"STRONG_BUY":0.25,"BUY":0.15,"WATCHLIST":0.05}.get(bucket,0)

# ==========================================================
# MORNING ENGINE — FULL NEWS INTEGRATION
# ==========================================================
def morning_run():
    send_msg("🌅 *Morning Scan Started*")

    # ---- NEWS ANALYSIS ----
    news_items = fetch_market_news()
    sentiment = analyze_news_sentiment(news_items)
    send_msg(format_news_block(news_items))

    noise = sentiment["noise"]
    bias = sentiment["bias"]
    sector_bias = sentiment["sector_bias"]

    exposure_cap = BASE_EXPOSURE_CAP
    strong_buy_allowed = True

    if bias == "NEGATIVE":
        exposure_cap *= 0.75

    if noise > 0.6:
        strong_buy_allowed = False

    # ---- STOCK UNIVERSE ----
    STOCKS = {
        "HDFCBANK.NS":"BANK","ICICIBANK.NS":"BANK","SBIN.NS":"BANK",
        "INFY.NS":"IT","TCS.NS":"IT",
        "RELIANCE.NS":"ENERGY","LT.NS":"INFRA",
        "TATASTEEL.NS":"METAL","JSWSTEEL.NS":"METAL",
        "SUNPHARMA.NS":"PHARMA"
    }

    data = batch_download(STOCKS.keys())
    ranked = []

    for sym, df in data.items():
        score, health = score_stock(df)

        # Sector news penalty
        if sector_bias.get(STOCKS[sym]) == "NEGATIVE":
            score -= 15

        bucket = assign_bucket(score, strong_buy_allowed)

        ranked.append({
            "symbol": sym,
            "score": score,
            "bucket": bucket,
            "health": health,
            "sector": STOCKS[sym]
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)

    total_alloc = 0
    sector_alloc = defaultdict(float)
    final = []

    for s in ranked:
        if len(final) == 5:
            break

        size = position_size(s["bucket"])
        if size == 0: continue
        if total_alloc + size > exposure_cap: continue
        if sector_alloc[s["sector"]] + size > SECTOR_CAP: continue

        total_alloc += size
        sector_alloc[s["sector"]] += size
        s["size"] = round(size * 100, 1)
        final.append(s)

    # ---- STATE (IDEMPOTENT) ----
    state_ws.clear()
    state_ws.append_row(["health_state", json.dumps({
        "date": ist_today(),
        "noise": noise,
        "bias": bias
    })])

    stocks_ws.clear()
    stocks_ws.append_row(["symbol","score","bucket","size","health","sector"])
    for s in final:
        stocks_ws.append_row([s[k] for k in ["symbol","score","bucket","size","health","sector"]])

    if not final:
        send_msg("⚠️ *Risk-Off Day*\n💤 No deployable opportunities.")
        return

    msg = [f"🏛️ *Top Picks — {ist_today()}*", ""]
    for s in final:
        msg.append(f"• *{s['symbol']}* | {s['bucket']} | {s['score']} | {s['size']}%")

    send_msg("\n".join(msg))

# ==========================================================
# EOD ENGINE
# ==========================================================
def eod_run():
    send_msg("📊 *EOD Analytics Ready*")

    prev = {r["symbol"]: int(r["score"]) for r in stocks_ws.get_all_records()}
    if not prev:
        return

    data = batch_download(prev.keys())
    for sym, df in data.items():
        score, _ = score_stock(df)
        if prev[sym] - score >= EXIT_SCORE_DROP:
            send_msg(f"❌ *EXIT*: {sym} | Score Drop {prev[sym]-score}")
            history_ws.append_row([ist_today(), sym, "EXIT", f"{prev[sym]} → {score}"])

# ==========================================================
# BOOTSTRAP
# ==========================================================
if __name__ == "__main__":
    keep_alive(eod_callback=eod_run)
    morning_run()
    while True:
        time.sleep(3600)
