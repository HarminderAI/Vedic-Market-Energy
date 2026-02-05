# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL (2026 PRODUCTION)
# ==========================================================

import os, json, time, datetime, threading
import requests, pytz
import yfinance as yf
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

BASE_GLOBAL_EXPOSURE = 0.90
SECTOR_CAP = 0.50
EXIT_SCORE_DROP = 15
MAX_WORKERS = 5

msg_lock = threading.Lock()

# ==========================================================
# TELEGRAM (DEDUP + MARKDOWN)
# ==========================================================
def send_msg(text):
    with msg_lock:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown"
            },
            timeout=10
        )

# ==========================================================
# GOOGLE SHEETS (SAFE + IDEMPOTENT)
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
    for _ in range(2):
        try:
            df = yf.download(ticker, period=f"{days}d", auto_adjust=True, progress=False)
            if df is None or df.empty:
                return None
            if df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            return df.dropna()
        except:
            time.sleep(2)
    return None

def batch_download(tickers):
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(safe_download, t): t for t in tickers}
        for f in as_completed(futures):
            df = f.result()
            if df is not None:
                out[futures[f]] = df
    return out

# ==========================================================
# INDICATORS
# ==========================================================
def trend_health(df):
    close = df["Close"]
    if len(close) < 25:
        return "UNKNOWN", 0
    ema = ta.ema(close, 20).dropna()
    if ema.empty:
        return "UNKNOWN", 0
    stretch = (close.iloc[-1] - ema.iloc[-1]) / ema.iloc[-1] * 100
    if stretch > 4: return "OVERSTRETCHED", stretch
    if stretch < -2: return "DEEP_PULLBACK", stretch
    return "HEALTHY", stretch

def score_stock(df):
    rsi = ta.rsi(df["Close"], 14).dropna()
    rsi_val = rsi.iloc[-1] if not rsi.empty else 50
    health, _ = trend_health(df)

    score = 30 if rsi_val > 60 else 15 if rsi_val > 50 else 5
    if health == "OVERSTRETCHED": score -= 20
    if health == "HEALTHY": score += 10

    return max(0, min(100, int(score))), health

def assign_bucket(score, strong_buy_allowed=True):
    if score >= 80 and strong_buy_allowed: return "STRONG_BUY"
    if score >= 65: return "BUY"
    if score >= 50: return "WATCHLIST"
    return "AVOID"

def position_size(bucket):
    return {"STRONG_BUY":0.25,"BUY":0.15,"WATCHLIST":0.05}.get(bucket,0)

# ==========================================================
# MORNING ENGINE (NEWS FULLY INTEGRATED)
# ==========================================================
def morning_run():
    # ---- DEDUP CHECK ----
    for r in state_ws.get_all_records():
        if r["key"] == "last_morning_run" and r["value"] == ist_today():
            return

    send_msg("🌅 *Morning Scan Started*")

    # ---- NEWS ANALYSIS ----
    news = fetch_market_news()
    send_msg(format_news_block(news))

    bias = news["overall"]
    noise = news["noise"]
    sector_sentiment = news["sector_map"]

    GLOBAL_EXPOSURE = BASE_GLOBAL_EXPOSURE
    STRONG_BUY_ALLOWED = True

    if bias < -0.2:
        GLOBAL_EXPOSURE *= 0.75
    if noise > 0.6:
        STRONG_BUY_ALLOWED = False

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

        # Sector sentiment penalty
        sector = STOCKS[sym]
        if sector in sector_sentiment and sector_sentiment[sector] < -0.2:
            score -= 10

        bucket = assign_bucket(score, STRONG_BUY_ALLOWED)
        if bucket != "AVOID":
            ranked.append({
                "symbol": sym,
                "score": score,
                "bucket": bucket,
                "health": health,
                "sector": sector
            })

    ranked.sort(key=lambda x: x["score"], reverse=True)

    total, sector_alloc = 0, defaultdict(float)
    final = []

    for s in ranked:
        if len(final) == 5: break
        size = position_size(s["bucket"])
        if total + size > GLOBAL_EXPOSURE: continue
        if sector_alloc[s["sector"]] + size > SECTOR_CAP: continue
        total += size
        sector_alloc[s["sector"]] += size
        s["size"] = round(size*100,1)
        final.append(s)

    # ---- STATE SAVE ----
    state_ws.clear()
    state_ws.append_row(["last_morning_run", ist_today()])

    stocks_ws.clear()
    stocks_ws.append_row(["symbol","score","bucket","size","health","sector"])
    for s in final:
        stocks_ws.append_row([s[k] for k in ["symbol","score","bucket","size","health","sector"]])

    if not final:
        send_msg("⚠️ *Risk-Off Day*\n💤 No deployable opportunities.")
        return

    msg = [f"🏛️ *Top Picks — {ist_today()}*",""]
    for s in final:
        msg.append(f"• *{s['symbol']}* | {s['bucket']} | {s['score']} | {s['size']}% | {s['health']}")
    send_msg("\n".join(msg))

# ==========================================================
# EOD ENGINE
# ==========================================================
def eod_run():
    send_msg("📊 *EOD Analytics Ready*")

    prev = {r["symbol"]: int(r["score"]) for r in stocks_ws.get_all_records()}
    if not prev: return

    data = batch_download(prev.keys())
    for sym, df in data.items():
        score,_ = score_stock(df)
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
