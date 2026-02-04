# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL (2026)
# ==========================================================

import os, json, time, datetime
import requests
import pytz
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import gspread
from google.oauth2.service_account import Credentials
from concurrent.futures import ThreadPoolExecutor, as_completed

from keep_alive import keep_alive
from news_logic import fetch_market_news, format_news_block

# ==========================================================
# TIMEZONE (IST LOCK)
# ==========================================================

IST = pytz.timezone("Asia/Kolkata")

def ist_now():
    return datetime.datetime.now(IST)

def ist_today():
    return ist_now().date()

# ==========================================================
# CONFIG
# ==========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

SUCCESS_THRESHOLD = 0.20
EXIT_SCORE_DROP = 15

TOTAL_CAPITAL = 1.0
GLOBAL_EXPOSURE_CAP = 0.90

MAX_WORKERS = 5  # Yahoo-safe in 2026

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
# GOOGLE SHEETS (SAFE BOOTSTRAP)
# ==========================================================

def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_JSON, scopes=scopes)
    return gspread.authorize(creds)

def get_or_create_worksheet(spreadsheet, title, rows=100, cols=20):
    try:
        return spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        print(f"⚠️ Creating worksheet: {title}")
        ws = spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)

        HEADERS = {
            "state":   ["key", "value"],
            "history": ["date", "symbol", "score", "bucket", "result"],
            "stocks":  ["symbol", "score", "bucket", "size", "trend_health"],
            "sectors": ["date", "sector", "allocation", "accuracy"]
        }

        if title in HEADERS:
            ws.append_row(HEADERS[title])

        return ws

# ---- AUTH FIRST (FIXED ORDER) ----
gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

state_ws   = get_or_create_worksheet(sheet, "state")
history_ws = get_or_create_worksheet(sheet, "history")
stocks_ws  = get_or_create_worksheet(sheet, "stocks")
sector_ws  = get_or_create_worksheet(sheet, "sectors")

# ==========================================================
# SAFE DOWNLOAD (yfinance 1.1.0)
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

# ==========================================================
# THREADED BATCH DOWNLOAD
# ==========================================================

def batch_download(tickers, days=80):
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(safe_download, t, days): t
            for t in tickers
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                df = future.result()
                if df is not None:
                    results[sym] = df
            except:
                pass
    return results

# ==========================================================
# TREND HEALTH
# ==========================================================

def trend_health(df):
    ema20 = ta.ema(df["Close"], 20).iloc[-1]
    price = df["Close"].iloc[-1]
    stretch = (price - ema20) / ema20 * 100

    if stretch > 4:
        return "OVERSTRETCHED", round(stretch, 2)
    if stretch < -2:
        return "DEEP_PULLBACK", round(stretch, 2)
    return "HEALTHY", round(stretch, 2)

# ==========================================================
# STOCK SCORING
# ==========================================================

def score_stock(df):
    rsi = ta.rsi(df["Close"], 14).iloc[-1]
    health, stretch = trend_health(df)

    score = 0
    score += 30 if rsi > 60 else 15 if rsi > 50 else 5

    if health == "OVERSTRETCHED":
        score -= 20
    elif health == "HEALTHY":
        score += 10

    return max(0, min(100, int(score))), health

# ==========================================================
# BUCKET & POSITION SIZING
# ==========================================================

def assign_bucket(score):
    if score >= 80:
        return "STRONG_BUY"
    if score >= 65:
        return "BUY"
    if score >= 50:
        return "WATCHLIST"
    return "AVOID"

def position_size(bucket):
    return {
        "STRONG_BUY": 0.25,
        "BUY": 0.15,
        "WATCHLIST": 0.05
    }.get(bucket, 0)

# ==========================================================
# DYNAMIC UNIVERSE UPDATE
# ==========================================================

def dynamic_universe_update(active, reserve):
    updated = active.copy()

    prev_state = state_ws.get_all_records()
    prev_health = {}
    if prev_state:
        try:
            prev_health = json.loads(prev_state[0]["value"]).get("health", {})
        except:
            pass

    reserve_data = batch_download(reserve)

    for sym, df in reserve_data.items():
        score, health = score_stock(df)

        allow = False
        if health == "HEALTHY":
            allow = True
        if prev_health.get(sym) == "OVERSTRETCHED" and health == "HEALTHY":
            allow = True

        if score >= 65 and allow:
            weakest = min(updated, key=lambda x: x["score"])
            if weakest["score"] < score:
                updated.remove(weakest)
                updated.append({
                    "symbol": sym,
                    "score": score,
                    "bucket": assign_bucket(score),
                    "health": health
                })

    return updated

# ==========================================================
# MAIN
# ==========================================================

def main():
    print("🚀 Morning Report Running...")

    prev_state = state_ws.get_all_records()
    prev_top = []
    prev_health = {}

    if prev_state:
        try:
            payload = json.loads(prev_state[0]["value"])
            prev_top = payload.get("top", [])
            prev_health = payload.get("health", {})
        except:
            pass

    STOCK_UNIVERSE = [
        "HDFCBANK", "ICICIBANK", "SBIN",
        "INFY", "TCS",
        "RELIANCE", "LT",
        "TATASTEEL", "JSWSTEEL",
        "SUNPHARMA"
    ]

    RESERVE_UNIVERSE = [
        "AXISBANK", "KOTAKBANK",
        "WIPRO", "HCLTECH",
        "ONGC", "POWERGRID"
    ]

    stock_data = batch_download(STOCK_UNIVERSE)

    ranked = []
    health_map = {}

    for sym, df in stock_data.items():
        score, health = score_stock(df)
        ranked.append({
            "symbol": sym,
            "score": score,
            "bucket": assign_bucket(score),
            "health": health
        })
        health_map[sym] = health

    ranked.sort(key=lambda x: x["score"], reverse=True)
    top5 = dynamic_universe_update(ranked[:5], RESERVE_UNIVERSE)

    alloc = 0
    allocations = []

    for s in sorted(top5, key=lambda x: x["score"], reverse=True):
        size = position_size(s["bucket"])
        size = min(size, GLOBAL_EXPOSURE_CAP - alloc)
        if size <= 0:
            continue
        alloc += size
        s["size"] = round(size * 100, 1)
        allocations.append(s)

    state_ws.clear()
    state_ws.append_row([
        "state",
        json.dumps({
            "date": str(ist_today()),
            "top": allocations,
            "health": health_map
        })
    ])

    lines = ["🏛️ Institutional Stock Advisor\n"]
    for s in allocations:
        lines.append(
            f"{s['symbol']} | {s['bucket']} | "
            f"Score: {s['score']} | "
            f"Size: {s['size']}% | "
            f"Trend: {s['health']}"
        )

    send_msg("\n".join(lines))

# ==========================================================
# KEEP ALIVE
# ==========================================================

if __name__ == "__main__":
    keep_alive(lambda: send_msg("📊 EOD Analytics Ready"))
    main()
    while True:
        time.sleep(3600)
