# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL PRODUCTION (2026)
# ==========================================================

import os, json, time, datetime, threading, io, hashlib
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

BASE_GLOBAL_EXPOSURE = 0.90
MAX_WORKERS = 5
YF_THROTTLE = 0.4   # seconds between Yahoo calls

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
        ws = sheet.worksheet(name)
    except:
        ws = sheet.add_worksheet(name, rows=500, cols=len(headers))
        ws.append_row(headers)
    return ws

state_ws   = safe_sheet("state", ["key", "value"])
stocks_ws  = safe_sheet("stocks", ["symbol","score","bucket","vol_ratio","squeeze"])
ohlc_ws    = safe_sheet("ohlc_cache", ["symbol","date","data"])

# ==========================================================
# STATE SHEET SANITY (AUTO-HEAL)
# ==========================================================
def ensure_state_headers():
    headers = state_ws.row_values(1)
    if headers != ["key", "value"]:
        state_ws.clear()
        state_ws.append_row(["key", "value"])

ensure_state_headers()

def read_state():
    rows = state_ws.get_all_records()
    state = {}
    for r in rows:
        k = r.get("key")
        v = r.get("value")
        if k and v:
            state[k] = v
    return state

def write_state(key, value):
    rows = state_ws.get_all_records()
    for i, r in enumerate(rows, start=2):
        if r.get("key") == key:
            state_ws.update_cell(i, 2, value)
            return
    state_ws.append_row([key, value])

# ==========================================================
# TELEGRAM (DEDUP + THREAD SAFE)
# ==========================================================
msg_lock = threading.Lock()

def send_msg(text, dedup_key=None, state_map=None):
    with msg_lock:
        if dedup_key and state_map:
            if state_map.get(dedup_key) == ist_today():
                return
            write_state(dedup_key, ist_today())

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )

# ==========================================================
# UNIVERSE — NIFTY 200
# ==========================================================
def load_nifty_200():
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    df = pd.read_csv(io.StringIO(r.text))
    return [f"{s}.NS" for s in df["Symbol"].tolist()]

# ==========================================================
# OHLC CACHE (DAILY)
# ==========================================================
def get_cached_ohlc(symbol):
    rows = ohlc_ws.get_all_records()
    for r in rows:
        if r.get("symbol") == symbol and r.get("date") == ist_today():
            return pd.DataFrame(json.loads(r["data"]))
    return None

def set_cached_ohlc(symbol, df):
    payload = json.dumps(df.to_dict())
    ohlc_ws.append_row([symbol, ist_today(), payload])

# ==========================================================
# MARKET DATA (THROTTLED)
# ==========================================================
yf_lock = threading.Lock()

def safe_download(symbol, days=90):
    cached = get_cached_ohlc(symbol)
    if cached is not None:
        return cached

    with yf_lock:
        time.sleep(YF_THROTTLE)
        try:
            df = yf.download(symbol, period=f"{days}d", auto_adjust=True, progress=False)
            if df is None or df.empty:
                return None
            if df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            df = df.dropna()
            set_cached_ohlc(symbol, df)
            return df
        except Exception as e:
            print("YF error:", symbol, e)
            return None

def batch_download(symbols):
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(safe_download, s): s for s in symbols}
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
        return "UNKNOWN"
    ema = ta.ema(close, length=20)
    if ema is None or ema.dropna().empty:
        return "UNKNOWN"
    stretch = (close.iloc[-1] - ema.dropna().iloc[-1]) / ema.iloc[-1] * 100
    if stretch > 4:
        return "OVERSTRETCHED"
    return "HEALTHY"

def score_stock(df):
    try:
        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]
        vol   = df["Volume"]

        if len(close) < 30:
            return 0, 50, 1.0, False, False

        rsi = ta.rsi(close, 14)
        rsi_val = rsi.dropna().iloc[-1] if rsi is not None else 50

        health = trend_health(df)

        avg_vol = vol.rolling(20).mean().iloc[-1]
        vol_ratio = round(vol.iloc[-1] / avg_vol, 2) if avg_vol and avg_vol > 0 else 1.0

        bb = ta.bbands(close, 20, 2)
        kc = ta.kc(high, low, close, 20, 1.5)

        squeeze = False
        breakout_up = False

        if bb is not None and kc is not None:
            bb = bb.dropna()
            kc = kc.dropna()
            if not bb.empty and not kc.empty:
                squeeze = (
                    bb.iloc[-1]["BBU_20_2.0"] < kc.iloc[-1]["KCUe_20_1.5"]
                    and bb.iloc[-1]["BBL_20_2.0"] > kc.iloc[-1]["KCLe_20_1.5"]
                )
                breakout_up = close.iloc[-1] > bb.iloc[-1]["BBU_20_2.0"]

        score = 30 if rsi_val > 60 else 15 if rsi_val > 50 else 5
        if health == "HEALTHY":
            score += 10
        if vol_ratio >= 2:
            score += 15
        elif vol_ratio >= 1.5:
            score += 10
        if squeeze:
            score += 5

        return min(100, score), int(rsi_val), vol_ratio, squeeze, breakout_up

    except Exception as e:
        print("Score error:", e)
        return 0, 50, 1.0, False, False

# ==========================================================
# MORNING ENGINE
# ==========================================================
def morning_run():
    state = read_state()
    if state.get("last_morning_run") == ist_today():
        return

    send_msg("🌅 *Morning Scan Started*", "morning_start", state)

    news = fetch_market_news()
    send_msg(format_news_block(news), "news", state)

    strong_buy_ok = not (news["noise"] > 0.6 or news["overall"] < -0.2)

    universe = load_nifty_200()
    data = batch_download(universe)

    ranked = []
    for sym, df in data.items():
        score, rsi, vol_ratio, squeeze, breakout = score_stock(df)

        if score >= 80 and vol_ratio >= 1.5 and breakout and not squeeze and strong_buy_ok:
            bucket = "STRONG_BUY"
        elif score >= 65:
            bucket = "BUY"
        elif score >= 50:
            bucket = "WATCHLIST"
        else:
            continue

        ranked.append((sym, score, bucket, vol_ratio, squeeze))

    ranked.sort(key=lambda x: x[1], reverse=True)
    top = ranked[:5]

    stocks_ws.clear()
    stocks_ws.append_row(["symbol","score","bucket","vol_ratio","squeeze"])
    for r in top:
        stocks_ws.append_row(list(r))

    if not top:
        send_msg("⚠️ *Risk-Off Day*\nNo deployable setups.", "riskoff", state)
    else:
        msg = [f"🏛️ *Top Picks — {ist_today()}*", ""]
        for r in top:
            msg.append(f"• *{r[0]}* | {r[2]} | Vol {r[3]}x")
        send_msg("\n".join(msg), "top_picks", state)

    write_state("last_morning_run", ist_today())

# ==========================================================
# EOD
# ==========================================================
def eod_run():
    send_msg("📊 *EOD Analytics Ready*", "eod", read_state())

# ==========================================================
# BOOTSTRAP
# ==========================================================
if __name__ == "__main__":
    keep_alive(eod_callback=eod_run)
    morning_run()
    while True:
        time.sleep(3600)
