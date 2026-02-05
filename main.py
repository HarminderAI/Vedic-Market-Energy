# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL STABLE (2026)
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

BASE_GLOBAL_EXPOSURE = 0.90
MAX_WORKERS = 4                 # SAFE for Yahoo
YF_THROTTLE = 0.7               # micro-throttle (seconds)

# ==========================================================
# THREAD LOCKS
# ==========================================================
msg_lock = threading.Lock()
yf_lock = threading.Lock()
ohlc_lock = threading.Lock()

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

state_ws = safe_sheet("state", ["key", "value"])
stocks_ws = safe_sheet("stocks", ["symbol","score","bucket","vol_ratio","squeeze"])
ohlc_ws = safe_sheet("ohlc_cache", ["symbol","date","data"])

# ==========================================================
# STATE HELPERS
# ==========================================================
def read_state():
    rows = state_ws.get_all_records()
    return {r.get("key"): r.get("value") for r in rows if "key" in r}

def write_state(key, value):
    rows = state_ws.get_all_records()
    for i, r in enumerate(rows, start=2):
        if r.get("key") == key:
            state_ws.update_cell(i, 2, value)
            return
    state_ws.append_row([key, value])

# ==========================================================
# TELEGRAM (DEDUP SAFE)
# ==========================================================
def send_msg(text, key=None):
    with msg_lock:
        state = read_state()
        if key and state.get(key) == ist_today():
            return
        if key:
            write_state(key, ist_today())

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )

# ==========================================================
# NIFTY 200 UNIVERSE
# ==========================================================
def load_nifty_200():
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    df = pd.read_csv(io.StringIO(r.text))
    return [f"{s}.NS" for s in df["Symbol"].tolist()]

# ==========================================================
# OHLC CACHE (IN-MEMORY)
# ==========================================================
ohlc_cache = {}

def load_ohlc_cache():
    rows = ohlc_ws.get_all_records()
    for r in rows:
        if r.get("date") == ist_today():
            try:
                ohlc_cache[r["symbol"]] = json.loads(r["data"])
            except:
                pass

def save_ohlc(symbol, df):
    df = df.copy()
    df.index = df.index.astype(str)  # CRITICAL FIX
    payload = json.dumps(df.to_dict())
    ohlc_ws.append_row([symbol, ist_today(), payload])

# ==========================================================
# MARKET DATA (RATE-LIMIT SAFE)
# ==========================================================
def safe_download(symbol, days=90):
    with ohlc_lock:
        cached = ohlc_cache.get(symbol)

    if cached:
        df = pd.DataFrame(cached)
        df.index = pd.to_datetime(df.index)
        return df

    with yf_lock:
        time.sleep(YF_THROTTLE)
        try:
            df = yf.download(
                symbol,
                period=f"{days}d",
                auto_adjust=True,
                progress=False,
                threads=False
            )
            if df is None or df.empty:
                return None

            if df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)

            df = df.dropna()
            df.index = df.index.astype(str)

            with ohlc_lock:
                ohlc_cache[symbol] = df.to_dict()

            save_ohlc(symbol, df)
            return pd.DataFrame(df).astype(float)

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
def score_stock(df):
    try:
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        if len(close) < 30:
            return 0, 1.0, False, False

        rsi = ta.rsi(close, 14)
        rsi_val = rsi.dropna().iloc[-1] if rsi is not None else 50

        vol_sma = volume.rolling(20).mean().iloc[-1]
        vol_ratio = round(volume.iloc[-1] / vol_sma, 2) if vol_sma > 0 else 1.0

        bb = ta.bbands(close, 20, 2)
        kc = ta.kc(high, low, close, 20, 1.5)

        squeeze = False
        breakout = False

        if bb is not None and kc is not None:
            bb = bb.dropna()
            kc = kc.dropna()
            if not bb.empty and not kc.empty:
                squeeze = (
                    bb.iloc[-1]["BBU_20_2.0"] < kc.iloc[-1]["KCUe_20_1.5"]
                    and bb.iloc[-1]["BBL_20_2.0"] > kc.iloc[-1]["KCLe_20_1.5"]
                )
                breakout = close.iloc[-1] > bb.iloc[-1]["BBU_20_2.0"]

        score = 30 if rsi_val > 60 else 15 if rsi_val > 50 else 5
        if vol_ratio >= 2: score += 15
        elif vol_ratio >= 1.5: score += 10
        if squeeze: score += 5

        return min(100, score), vol_ratio, squeeze, breakout

    except:
        return 0, 1.0, False, False

# ==========================================================
# MORNING ENGINE
# ==========================================================
def morning_run():
    state = read_state()
    if state.get("last_morning_run") == ist_today():
        return

    send_msg("🌅 *Morning Scan Started*", "morning")

    news = fetch_market_news()
    send_msg(format_news_block(news), "news")

    strong_buy_ok = not (news["noise"] > 0.6)

    load_ohlc_cache()
    universe = load_nifty_200()
    data = batch_download(universe)

    picks = []
    for sym, df in data.items():
        score, vol, squeeze, breakout = score_stock(df)
        if score >= 80 and vol >= 1.5 and breakout and not squeeze and strong_buy_ok:
            bucket = "STRONG_BUY"
        elif score >= 65:
            bucket = "BUY"
        elif score >= 50:
            bucket = "WATCHLIST"
        else:
            continue
        picks.append((sym, score, bucket, vol, squeeze))

    picks.sort(key=lambda x: x[1], reverse=True)
    picks = picks[:5]

    stocks_ws.clear()
    stocks_ws.append_row(["symbol","score","bucket","vol_ratio","squeeze"])
    for p in picks:
        stocks_ws.append_row(p)

    if not picks:
        send_msg("⚠️ *Risk-Off Day*\nNo deployable opportunities.", "riskoff")
    else:
        msg = [f"🏛️ *Top Picks — {ist_today()}*", ""]
        for p in picks:
            msg.append(f"• *{p[0]}* | {p[2]} | Vol {p[3]}x")
        send_msg("\n".join(msg), "picks")

    write_state("last_morning_run", ist_today())

# ==========================================================
# EOD ENGINE
# ==========================================================
def eod_run():
    send_msg("📊 *EOD Analytics Ready*", "eod")

# ==========================================================
# BOOTSTRAP
# ==========================================================
if __name__ == "__main__":
    keep_alive(eod_callback=eod_run)
    morning_run()
    while True:
        time.sleep(3600)
