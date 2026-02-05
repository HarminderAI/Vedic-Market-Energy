# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL PRODUCTION (2026)
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
MAX_WORKERS = 5          # SAFE for Render
YF_THROTTLE = 0.35       # micro-throttle (seconds)

# ==========================================================
# TELEGRAM (DEDUP + THREAD SAFE)
# ==========================================================
msg_lock = threading.Lock()

def send_msg(text, dedup_key=None, state_map=None):
    with msg_lock:
        if dedup_key and state_map is not None:
            if state_map.get(dedup_key) == ist_today():
                return
            _write_state(dedup_key, ist_today())
            state_map[dedup_key] = ist_today()

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
ohlc_ws   = safe_sheet("ohlc_cache", ["date","symbol","json"])

def _write_state(key, value):
    rows = state_ws.get_all_records()
    for i, r in enumerate(rows, start=2):
        if r["key"] == key:
            state_ws.update_cell(i, 2, value)
            return
    state_ws.append_row([key, value])

# ==========================================================
# UNIVERSE — NIFTY 200
# ==========================================================
def load_nifty_200():
    url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    df = pd.read_csv(io.StringIO(r.text))
    return [f"{s}.NS" for s in df["Symbol"].tolist()]

# ==========================================================
# DAILY OHLC CACHE
# ==========================================================
def get_cached_ohlc(symbol):
    rows = ohlc_ws.get_all_records()
    for r in rows:
        if r["symbol"] == symbol and r["date"] == ist_today():
            return pd.DataFrame(json.loads(r["json"]))
    return None

def set_cached_ohlc(symbol, df):
    payload = json.dumps(df.to_dict())
    if len(payload) < 45000:
        ohlc_ws.append_row([ist_today(), symbol, payload])

# ==========================================================
# MARKET DATA (with micro-throttle)
# ==========================================================
def safe_download(symbol, days=90):
    cached = get_cached_ohlc(symbol)
    if cached is not None:
        return cached

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
        set_cached_ohlc(symbol, df)
        return df
    except:
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
    close = df["Close"].dropna()
    if len(close) < 25:
        return "UNKNOWN"

    ema = ta.ema(close, length=20)
    if ema is None or ema.dropna().empty:
        return "UNKNOWN"

    stretch = (close.iloc[-1] - ema.iloc[-1]) / ema.iloc[-1] * 100
    if stretch > 4:
        return "OVERSTRETCHED"
    if stretch < -2:
        return "DEEP_PULLBACK"
    return "HEALTHY"

def score_stock(df):
    close  = df["Close"].dropna()
    high   = df["High"].dropna()
    low    = df["Low"].dropna()
    volume = df["Volume"].dropna()

    if len(close) < 30:
        return 0, 1.0, False, False

    rsi = ta.rsi(close, length=14)
    rsi_val = rsi.dropna().iloc[-1] if rsi is not None else 50

    health = trend_health(df)

    avg_vol = volume.rolling(20).mean().iloc[-1]
    vol_ratio = round(volume.iloc[-1] / avg_vol, 2) if avg_vol and avg_vol > 0 else 1.0

    bb = ta.bbands(close, length=20)
    kc = ta.kc(high, low, close, length=20)

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
    if vol_ratio >= 1.5:
        score += 10
    if squeeze:
        score += 5

    return min(100, score), vol_ratio, squeeze, breakout_up

# ==========================================================
# MORNING ENGINE
# ==========================================================
def morning_run():
    state_rows = state_ws.get_all_records()

    state_map = {}
    for r in state_rows:
    k = r.get("key")
    v = r.get("value")
    if k and v:
        state_map[k] = v

    if state_map.get("last_morning_run") == ist_today():
        return

    send_msg("🌅 *Morning Scan Started*", "morning_start", state_map)

    news = fetch_market_news()
    send_msg(format_news_block(news), "news", state_map)

    strong_buy_ok = not (news["overall"] < -0.2 or news["noise"] > 0.6)

    universe = load_nifty_200()
    data = batch_download(universe)

    ranked = []
    for sym, df in data.items():
        score, vol, squeeze, breakout = score_stock(df)

        if score >= 80 and vol >= 1.5 and breakout and not squeeze and strong_buy_ok:
            bucket = "STRONG_BUY"
        elif score >= 65:
            bucket = "BUY"
        elif score >= 50:
            bucket = "WATCH"
        else:
            continue

        ranked.append((sym, score, bucket, vol, squeeze))

    ranked.sort(key=lambda x: x[1], reverse=True)
    final = ranked[:5]

    stocks_ws.clear()
    stocks_ws.append_row(["symbol","score","bucket","vol_ratio","squeeze"])
    for r in final:
        stocks_ws.append_row(list(r))

    if final:
        msg = [f"🏛️ *Top Picks — {ist_today()}*", ""]
        for s in final:
            msg.append(f"• *{s[0]}* | {s[2]} | Vol {s[3]}x")
        send_msg("\n".join(msg), "top_picks", state_map)
    else:
        send_msg("⚠️ *Risk-Off Day*\nNo deployable setups.", "riskoff", state_map)

    _write_state("last_morning_run", ist_today())

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
