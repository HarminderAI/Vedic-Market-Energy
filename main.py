# ==========================================================
# 🏛️ INSTITUTIONAL STOCK ADVISOR BOT — FINAL (2026 STABLE)
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
# GOOGLE SHEETS — HARDENED
# ==========================================================
def sheet_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(SERVICE_JSON, scopes=scopes)
    return gspread.authorize(creds)

gc = sheet_client()
sheet = gc.open_by_key(GOOGLE_SHEET_ID)

def safe_sheet(ws_name, headers):
    global gc, sheet
    try:
        return sheet.worksheet(ws_name)
    except:
        gc = sheet_client()
        sheet = gc.open_by_key(GOOGLE_SHEET_ID)
        try:
            return sheet.worksheet(ws_name)
        except:
            ws = sheet.add_worksheet(ws_name, rows=200, cols=20)
            ws.append_row(headers)
            return ws

state_ws   = safe_sheet("state", ["key","value"])
stocks_ws  = safe_sheet("stocks", ["symbol","score","bucket","size","health","sector"])
history_ws = safe_sheet("history", ["date","symbol","event","detail"])

# ==========================================================
# MARKET DATA
# ==========================================================
def safe_download(ticker, days=80):
    try:
        # auto_adjust=True ensures splits/dividends are handled
        df = yf.download(
            ticker,
            period=f"{days}d",
            progress=False,
            auto_adjust=True
        )

        if df is None or df.empty:
            return None

        # Robust MultiIndex flattening (2026-safe)
        if df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)

        if "Close" not in df:
            return None

        return df.dropna()

    except Exception as e:
        print(f"Download error for {ticker}: {e}")
        return None

def batch_download(tickers):
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(safe_download, t): t for t in tickers}
        for f in as_completed(futures):
            sym = futures[f]
            try:
                df = f.result()
                if df is not None:
                    out[sym] = df
            except:
                pass
    return out

# ==========================================================
# INDICATORS
# ==========================================================
def trend_health(df):
    close = df["Close"].dropna()
    if len(close) < 25:
        return "UNKNOWN", 0.0

    ema_series = ta.ema(close, length=20)
    if ema_series is None or ema_series.dropna().empty:
        return "UNKNOWN", 0.0

    ema = ema_series.dropna().iloc[-1]
    price = close.iloc[-1]
    stretch = (price - ema) / ema * 100

    if stretch > 4:
        return "OVERSTRETCHED", round(stretch, 2)
    if stretch < -2:
        return "DEEP_PULLBACK", round(stretch, 2)
    return "HEALTHY", round(stretch, 2)

def score_stock(df):
    rsi_series = ta.rsi(df["Close"], length=14)
    rsi = rsi_series.dropna().iloc[-1] if rsi_series is not None and not rsi_series.dropna().empty else 50

    health, _ = trend_health(df)
    score = 30 if rsi > 60 else 15 if rsi > 50 else 5
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
    return {"STRONG_BUY":0.25,"BUY":0.15,"WATCHLIST":0.05}.get(bucket,0)

# ==========================================================
# MORNING ENGINE (FIXED STATE READ)
# ==========================================================
def morning_run():
    send_msg("🌅 Morning Scan Started")

    STOCKS = {
        "HDFCBANK.NS":"BANK","ICICIBANK.NS":"BANK","SBIN.NS":"BANK",
        "INFY.NS":"IT","TCS.NS":"IT",
        "RELIANCE.NS":"ENERGY","LT.NS":"INFRA",
        "TATASTEEL.NS":"METAL","JSWSTEEL.NS":"METAL",
        "SUNPHARMA.NS":"PHARMA"
    }

    # ---- SAFE STATE READ (POSITIONAL) ----
    prev_health = {}
    rows = state_ws.get_all_values()[1:]  # skip header
    for r in rows:
        if len(r) >= 2 and r[0] == "health_state":
            try:
                prev_health = json.loads(r[1]).get("health", {})
            except:
                pass

    data = batch_download(STOCKS.keys())
    ranked = []

    for sym, df in data.items():
        score, health = score_stock(df)
        allow = health == "HEALTHY" or (prev_health.get(sym) == "OVERSTRETCHED" and health == "HEALTHY")
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

    for s in ranked:
        if len(final) == 5:
            break
        size = position_size(s["bucket"])
        if size == 0: continue
        if total_alloc + size > GLOBAL_EXPOSURE_CAP: continue
        if sector_alloc[s["sector"]] + size > SECTOR_CAP:
            send_msg(f"⚠️ Sector Concentration Alert: {s['sector']}")
            continue

        total_alloc += size
        sector_alloc[s["sector"]] += size
        s["size"] = round(size * 100, 1)
        final.append(s)

    # ---- SAVE STATE (NO CLEAR) ----
    found = False
    for i, r in enumerate(rows, start=2):
        if r[0] == "health_state":
            state_ws.update_cell(i, 2, json.dumps({
                "date": ist_today(),
                "health": {s["symbol"]: s["health"] for s in ranked}
            }))
            found = True
            break
    if not found:
        state_ws.append_row([
            "health_state",
            json.dumps({"date": ist_today(), "health": {s["symbol"]: s["health"] for s in ranked}})
        ])

    stocks_ws.clear()
    stocks_ws.append_row(["symbol","score","bucket","size","health","sector"])
    for s in final:
        stocks_ws.append_row([s[k] for k in ["symbol","score","bucket","size","health","sector"]])

    if not final:
        send_msg("⚠️ Risk-Off Day: No deployable opportunities.\n💤 Capital preserved.")
        return

    msg = [f"🏛️ Top Picks — {ist_today()}", ""]
    for s in final:
        msg.append(f"• {s['symbol']} | {s['bucket']} | {s['score']} | {s['size']}% | {s['health']}")
    send_msg("\n".join(msg))

# ==========================================================
# EOD ENGINE
# ==========================================================
def eod_run():
    send_msg("📊 EOD Analytics Ready")

    prev = {r["symbol"]: int(r["score"]) for r in stocks_ws.get_all_records()}
    if not prev:
        return

    data = batch_download(prev.keys())
    for sym, df in data.items():
        score, _ = score_stock(df)
        if prev[sym] - score >= EXIT_SCORE_DROP:
            send_msg(f"❌ EXIT SIGNAL: {sym} | Score Drop {prev[sym] - score}")
            history_ws.append_row([ist_today(), sym, "EXIT", f"{prev[sym]} → {score}"])

# ==========================================================
# BOOTSTRAP
# ==========================================================
if __name__ == "__main__":
    keep_alive(eod_callback=eod_run)
    morning_run()
    while True:
        time.sleep(3600)
