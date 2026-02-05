# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — PRODUCTION (STABLE)
# ==========================================================

import os, json, time, datetime, threading, io
import requests, pytz
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import gspread
from google.oauth2.service_account import Credentials
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
    return ist_now().date().isoformat()

# ==========================================================
# CONFIG
# ==========================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_JSON = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))

MAX_WORKERS = 5
BASE_GLOBAL_EXPOSURE = 0.9

# ==========================================================
# TELEGRAM (DEDUP SAFE)
# ==========================================================
msg_lock = threading.Lock()

def send_msg(text, dedup_key=None, state_map=None):
    with msg_lock:
        if dedup_key and state_map is not None:
            if state_map.get(dedup_key) == ist_today():
                return
            _write_state(dedup_key, ist_today())

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )

# ==========================================================
# GOOGLE SHEETS
# ==========================================================
def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_JSON, scopes=scopes)
    return gspread.authorize(creds)

gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

def safe_sheet(name, headers):
    try:
        return sheet.worksheet(name)
    except:
        ws = sheet.add_worksheet(name, rows=500, cols=len(headers))
        ws.append_row(headers)
        return ws

state_ws  = safe_sheet("state", ["key", "value"])
stocks_ws = safe_sheet("stocks", ["symbol","score","bucket","vol_ratio","squeeze"])
history_ws = safe_sheet("history", ["date","symbol","event","detail"])

def _write_state(key, value):
    rows = state_ws.get_all_records()
    for i, r in enumerate(rows, start=2):
        if r.get("key") == key:
            state_ws.update_cell(i, 2, value)
            return
    state_ws.append_row([key, value])

# ==========================================================
# UNIVERSE (WITH FALLBACK)
# ==========================================================
HARDCODED_FALLBACK = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS"
]

def load_nifty_200():
    try:
        url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        df = pd.read_csv(io.StringIO(r.text))
        return [f"{s}.NS" for s in df["Symbol"].tolist()]
    except Exception:
        return HARDCODED_FALLBACK

# ==========================================================
# MARKET DATA (MICRO-THROTTLED)
# ==========================================================
def safe_download(symbol, days=90):
    try:
        time.sleep(0.25)
        df = yf.download(symbol, period=f"{days}d", auto_adjust=True, progress=False)
        if df is None or df.empty:
            return None
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df.dropna()
    except Exception:
        return None

def batch_download(symbols):
    data = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(safe_download, s): s for s in symbols}
        for f in as_completed(futures):
            df = f.result()
            if df is not None:
                data[futures[f]] = df
    return data

# ==========================================================
# INDICATORS
# ==========================================================
def trend_health(df):
    close = df["Close"]
    ema = ta.ema(close, length=20)
    if ema is None or ema.dropna().empty:
        return "UNKNOWN"

    price = close.iloc[-1]
    ema_val = ema.dropna().iloc[-1]
    stretch = (price - ema_val) / ema_val * 100

    if stretch > 4:
        return "OVERSTRETCHED"
    if stretch < -2:
        return "DEEP_PULLBACK"
    return "HEALTHY"

def score_stock(df):
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    if len(close) < 30:
        return 0, 50, 1.0, False, False

    score = 0

    # RSI
    rsi = ta.rsi(close, length=14)
    rsi_val = int(rsi.dropna().iloc[-1]) if rsi is not None else 50

    if rsi_val >= 70:
        score += 30
    elif rsi_val >= 60:
        score += 22
    elif rsi_val >= 50:
        score += 12

    # Trend
    health = trend_health(df)
    if health == "HEALTHY":
        score += 25
    elif health == "DEEP_PULLBACK":
        score += 10
    elif health == "OVERSTRETCHED":
        score -= 15

    # Volume
    avg_vol = volume.rolling(20).mean().iloc[-1]
    vol_ratio = round(volume.iloc[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

    if vol_ratio >= 2.5:
        score += 20
    elif vol_ratio >= 2.0:
        score += 15
    elif vol_ratio >= 1.5:
        score += 8

    # Volatility (CORRECT PARAMETERS)
    bb = ta.bbands(close, length=20, std=2)
    kc = ta.kc(high, low, close, length=20, scalar=1.5)

    squeeze = False
    breakout = False

    if bb is not None and kc is not None:
        upper_bb = bb.filter(like="BBU").iloc[-1, 0]
        lower_bb = bb.filter(like="BBL").iloc[-1, 0]
        upper_kc = kc.filter(like="KCU").iloc[-1, 0]
        lower_kc = kc.filter(like="KCL").iloc[-1, 0]

        squeeze = upper_bb < upper_kc and lower_bb > lower_kc
        if squeeze:
            score += 10

        if close.iloc[-1] > upper_bb:
            breakout = True
            score += 10

    score = max(0, min(100, score))
    return score, rsi_val, vol_ratio, squeeze, breakout

# ==========================================================
# MORNING ENGINE (WITH WATCHLIST)
# ==========================================================
def morning_run():
    state_rows = state_ws.get_all_records()
    state_map = {r["key"]: r["value"] for r in state_rows if "key" in r}

    if state_map.get("last_morning_run") == ist_today():
        return

    send_msg("🌅 *Morning Scan Started*", "morning_start", state_map)

    news = fetch_market_news()
    send_msg(format_news_block(news), "news", state_map)

    universe = load_nifty_200()
    data = batch_download(universe)

    buys, watchlist = [], []

    for sym, df in data.items():
        score, rsi, vol_ratio, squeeze, breakout = score_stock(df)

        if score >= 80 and breakout:
            buys.append((sym, score, "STRONG_BUY", vol_ratio, squeeze))
        elif score >= 65:
            buys.append((sym, score, "BUY", vol_ratio, squeeze))
        elif score >= 55:
            watchlist.append((sym, score, vol_ratio))

    stocks_ws.clear()
    stocks_ws.append_row(["symbol","score","bucket","vol_ratio","squeeze"])

    for s in buys:
        stocks_ws.append_row([s[0], s[1], s[2], s[3], s[4]])

    if buys:
        msg = ["🏛️ *Deployable Opportunities*", ""]
        for s in buys[:5]:
            msg.append(f"• *{s[0]}* | {s[2]} | Score {s[1]}")
        send_msg("\n".join(msg), "deployables", state_map)

    if watchlist:
        msg = ["👀 *Pre-Breakout Watchlist*", ""]
        for s in watchlist[:5]:
            msg.append(f"• *{s[0]}* | Score {s[1]} | Vol {s[2]}x")
        send_msg("\n".join(msg), "watchlist", state_map)

    if not buys and not watchlist:
        send_msg("⚠️ *Risk-Off Day*\nNo quality setups today.", "riskoff", state_map)

    _write_state("last_morning_run", ist_today())

# ==========================================================
# BOOTSTRAP
# ==========================================================
if __name__ == "__main__":
    keep_alive()
    morning_run()
    while True:
        time.sleep(3600)
