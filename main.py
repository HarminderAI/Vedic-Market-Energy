# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL (2026 HARDENED)
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
from news_logic import fetch_market_news, format_news_block

# ==========================================================
# TIMEZONE
# ==========================================================
IST = pytz.timezone("Asia/Kolkata")

def ist_now():
    return datetime.datetime.now(IST)

def ist_today():
    return str(ist_now().date())

# ==========================================================
# CONFIG
# ==========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

TOTAL_CAPITAL = 1.0
GLOBAL_EXPOSURE_CAP = 0.90
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
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print("Telegram error:", e)

# ==========================================================
# GOOGLE SHEETS
# ==========================================================
def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_JSON, scopes=scopes)
    return gspread.authorize(creds)

gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

def get_ws(name, headers):
    try:
        return sheet.worksheet(name)
    except:
        ws = sheet.add_worksheet(name, rows=200, cols=20)
        ws.append_row(headers)
        return ws

state_ws   = get_ws("state",   ["key", "value"])
stocks_ws  = get_ws("stocks",  ["symbol", "score", "bucket", "size", "health", "sector"])
history_ws = get_ws("history", ["date", "symbol", "event", "detail"])

# ==========================================================
# MARKET DATA
# ==========================================================
def safe_download(ticker, days=80):
    try:
        df = yf.download(ticker, period=f"{days}d", progress=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except:
        return None

def batch_download(tickers):
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut = {ex.submit(safe_download, t): t for t in tickers}
        for f in as_completed(fut):
            if f.result() is not None:
                out[fut[f]] = f.result()
    return out

# ==========================================================
# SCORING ENGINE
# ==========================================================
def trend_health(df):
    if len(df) < 25:
        return "UNKNOWN", 0
    ema = ta.ema(df["Close"], 20).iloc[-1]
    price = df["Close"].iloc[-1]
    stretch = (price - ema) / ema * 100

    if stretch > 4:
        return "OVERSTRETCHED", round(stretch, 2)
    if stretch < -2:
        return "DEEP_PULLBACK", round(stretch, 2)
    return "HEALTHY", round(stretch, 2)

def score_stock(df):
    rsi_series = ta.rsi(df["Close"], 14)
    rsi = 50 if rsi_series is None or pd.isna(rsi_series.iloc[-1]) else rsi_series.iloc[-1]

    health, _ = trend_health(df)
    score = 0

    score += 30 if rsi > 60 else 15 if rsi > 50 else 5
    if health == "OVERSTRETCHED":
        score -= 20
    elif health == "HEALTHY":
        score += 10

    return max(0, min(100, int(score))), health

def assign_bucket(score):
    if score >= 80: return "STRONG_BUY"
    if score >= 65: return "BUY"
    if score >= 50: return "WATCHLIST"
    return "AVOID"

def position_size(bucket):
    return {"STRONG_BUY": 0.25, "BUY": 0.15, "WATCHLIST": 0.05}.get(bucket, 0)

# ==========================================================
# MORNING ENGINE (FIXED TOP-5 LOGIC)
# ==========================================================
def morning_run():
    send_msg("🌅 Morning Scan Started")

    STOCKS = {
        "HDFCBANK.NS": "BANK", "ICICIBANK.NS": "BANK", "SBIN.NS": "BANK",
        "INFY.NS": "IT", "TCS.NS": "IT",
        "RELIANCE.NS": "ENERGY", "LT.NS": "INFRA",
        "TATASTEEL.NS": "METAL", "JSWSTEEL.NS": "METAL",
        "SUNPHARMA.NS": "PHARMA"
    }

    prev_state = {}
    rows = state_ws.get_all_records()
    if rows:
        try:
            prev_state = json.loads(rows[0]["value"]).get("health", {})
        except:
            prev_state = {}

    data = batch_download(STOCKS.keys())
    ranked = []

    for sym, df in data.items():
        score, health = score_stock(df)

        # Pullback re-entry condition
        allow = (
            health == "HEALTHY" or
            (prev_state.get(sym) == "OVERSTRETCHED" and health == "HEALTHY")
        )

        if allow:
            ranked.append({
                "symbol": sym,
                "score": score,
                "bucket": assign_bucket(score),
                "health": health,
                "sector": STOCKS[sym]
            })

    ranked.sort(key=lambda x: x["score"], reverse=True)

    total_alloc = 0
    sector_alloc = defaultdict(float)
    final = []

    # 🔥 FIX: iterate full ranked list until 5 filled
    for s in ranked:
        if len(final) == 5:
            break

        size = position_size(s["bucket"])
        if total_alloc + size > GLOBAL_EXPOSURE_CAP:
            continue
        if sector_alloc[s["sector"]] + size > SECTOR_CAP:
            send_msg(f"⚠️ Sector Concentration Alert: {s['sector']}")
            continue

        total_alloc += size
        sector_alloc[s["sector"]] += size
        s["size"] = round(size * 100, 1)
        final.append(s)

    # Persist state
    health_map = {s["symbol"]: s["health"] for s in ranked}
    state_ws.clear()
    state_ws.append_row(["state", json.dumps({"date": ist_today(), "health": health_map})])

    stocks_ws.clear()
    stocks_ws.append_row(["symbol", "score", "bucket", "size", "health", "sector"])
    for s in final:
        stocks_ws.append_row([s[k] for k in ["symbol","score","bucket","size","health","sector"]])

    msg = [f"🏛️ Top Picks — {ist_today()}", ""]
    for s in final:
        msg.append(
            f"• {s['symbol']} | {s['bucket']} | {s['score']} | {s['size']}% | {s['health']}"
        )

    send_msg("\n".join(msg))

# ==========================================================
# EOD ENGINE (EXIT + HEALTH PERSISTENCE)
# ==========================================================
def eod_run():
    send_msg("📊 EOD Analytics Ready")

    prev = {r["symbol"]: int(r["score"]) for r in stocks_ws.get_all_records()}
    data = batch_download(prev.keys())

    for sym, df in data.items():
        score, health = score_stock(df)

        if prev[sym] - score >= EXIT_SCORE_DROP:
            send_msg(f"❌ EXIT SIGNAL: {sym} | Score Drop {prev[sym] - score}")
            history_ws.append_row([ist_today(), sym, "EXIT", f"Score {prev[sym]} → {score}"])

# ==========================================================
# BOOTSTRAP
# ==========================================================
if __name__ == "__main__":
    keep_alive(eod_callback=eod_run)
    morning_run()

    while True:
        time.sleep(3600)
