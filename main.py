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
    return ist_now().date().isoformat()

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

# 5 is SAFE on Render (no IP blocks)
# Increase to 8 only if server has ≥2 vCPUs
MAX_WORKERS = 5

# ==========================================================
# TELEGRAM (DEDUP + THREAD SAFE)
# ==========================================================
msg_lock = threading.Lock()

def send_msg(text, dedup_key=None):
    with msg_lock:
        if dedup_key:
            for r in state_ws.get_all_records():
                if r.get("key") == dedup_key and r.get("value") == ist_today():
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

state_ws   = safe_sheet("state", ["key", "value"])
stocks_ws  = safe_sheet("stocks", ["symbol","score","bucket","size","health","sector","vol_ratio","squeeze"])
history_ws = safe_sheet("history", ["date","symbol","event","detail"])
archive_ws = safe_sheet("history_archive", ["date","symbol","event","detail"])

def _write_state(key, value):
    rows = state_ws.get_all_records()
    for i, r in enumerate(rows, start=2):
        if r.get("key") == key:
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
# MARKET DATA
# ==========================================================
def safe_download(ticker, days=90):
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
def score_stock(df):
    close = df["Close"].dropna()
    volume = df["Volume"].dropna()

    # -------- RSI (SAFE) --------
    rsi_series = ta.rsi(close, length=14)
    if rsi_series is None or rsi_series.dropna().empty:
        rsi_val = 50
    else:
        rsi_val = rsi_series.dropna().iloc[-1]

    # -------- TREND HEALTH --------
    health, _ = trend_health(df)

    # -------- VOLUME BREAKOUT --------
    if len(volume) < 20:
        vol_ratio = 1.0
    else:
        vol_sma = volume.rolling(20).mean()
        avg_vol = vol_sma.iloc[-1]
        vol_ratio = round(volume.iloc[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

    # -------- BOLLINGER + KELTNER (SQUEEZE) --------
    is_squeezed = False
    breakout_up = False

    bb = ta.bbands(close, length=20, std=2)
    kc = ta.kc(df["High"], df["Low"], close, length=20, scalar=1.5)

    if bb is not None and kc is not None:
        try:
            upper_bb = bb["BBU_20_2.0"].iloc[-1]
            lower_bb = bb["BBL_20_2.0"].iloc[-1]
            upper_kc = kc["KCUe_20_1.5"].iloc[-1]
            lower_kc = kc["KCLe_20_1.5"].iloc[-1]

            is_squeezed = upper_bb < upper_kc and lower_bb > lower_kc
            breakout_up = close.iloc[-1] > upper_bb

        except Exception:
            pass

    # -------- SCORING --------
    score = 30 if rsi_val > 60 else 15 if rsi_val > 50 else 5

    if health == "OVERSTRETCHED":
        score -= 20
    elif health == "HEALTHY":
        score += 10

    # Volume confirmation
    if vol_ratio >= 2.0:
        score += 15
    elif vol_ratio >= 1.5:
        score += 10

    # Squeeze charging bonus
    if is_squeezed:
        score += 5

    return (
        max(0, min(100, int(score))),
        round(rsi_val, 1),
        round(vol_ratio, 2),
        is_squeezed,
        breakout_up
    )

# ==========================================================
# HISTORY CLEANUP (90 DAYS)
# ==========================================================
def cleanup_history():
    rows = history_ws.get_all_records()
    cutoff = ist_now() - datetime.timedelta(days=90)

    keep = []
    for r in rows:
        d = datetime.datetime.fromisoformat(r["date"])
        if d < cutoff:
            archive_ws.append_row([r["date"], r["symbol"], r["event"], r["detail"]])
        else:
            keep.append([r["date"], r["symbol"], r["event"], r["detail"]])

    history_ws.clear()
    history_ws.append_row(["date","symbol","event","detail"])
    for row in keep:
        history_ws.append_row(row)

# ==========================================================
# MORNING ENGINE
# ==========================================================
def morning_run():
    if any(r.get("key") == "last_morning_run" and r.get("value") == ist_today()
           for r in state_ws.get_all_records()):
        return

    send_msg("🌅 *Morning Scan Started*", "morning_start")

    news = fetch_market_news()
    send_msg(format_news_block(news), "news")

    global_cap = BASE_GLOBAL_EXPOSURE
    strong_buy_ok = True
    if news["overall"] < -0.2:
        global_cap *= 0.75
    if news["noise"] > 0.6:
        strong_buy_ok = False

    universe = load_nifty_200()
    data = batch_download(universe)

    ranked = []
    for sym, df in data.items():
        score, rsi, vol_ratio, squeeze, breakout_up = score_stock(df)

        if score >= 80 and vol_ratio >= 1.5 and breakout_up and not squeeze and strong_buy_ok:
            bucket = "STRONG_BUY"
        elif score >= 65:
            bucket = "BUY"
        elif score >= 50:
            bucket = "WATCHLIST"
        else:
            continue

        ranked.append((sym, score, bucket, vol_ratio, squeeze))

    ranked.sort(key=lambda x: x[1], reverse=True)
    final = ranked[:5]

    stocks_ws.clear()
    stocks_ws.append_row(stocks_ws.row_values(1))
    for s in final:
        stocks_ws.append_row([s[0], s[1], s[2], "-", "-", "GENERIC", s[3], s[4]])

    if not final:
        send_msg("⚠️ *Risk-Off Day*\nNo deployable opportunities.", "riskoff")
    else:
        msg = [f"🏛️ *Top Picks — {ist_today()}*", ""]
        for s in final:
            msg.append(f"• *{s[0]}* | {s[2]} | Vol {s[3]}x")
        send_msg("\n".join(msg), "top_picks")

    _write_state("last_morning_run", ist_today())
    cleanup_history()

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
