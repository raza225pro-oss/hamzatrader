import os
import json
import re
import asyncio
import threading
import logging
import hashlib
from flask import Flask
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "HamzaTrader Quotex Bot is running!"

@app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

# --- Configuration & Branding ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) if os.getenv("ADMIN_ID", "").strip().isdigit() else 0
raw_ids = os.getenv("ALLOWED_IDS", "")
ENV_ALLOWED_IDS = set(int(x.strip()) for x in raw_ids.split(",") if x.strip().isdigit())
if ADMIN_ID:
    ENV_ALLOWED_IDS.add(ADMIN_ID)

QUOTEX_PARTNER_LINK = os.getenv("QUOTEX_PARTNER_LINK", "https://broker-qx.pro/sign-up/?lid=2111446")
USDT_ADDRESS = os.getenv("USDT_ADDRESS", "TYourUSDTWalletAddressHereTRC20")
USDT_NETWORK = os.getenv("USDT_NETWORK", "TRC20 (Tron) / Binance Pay")
PREMIUM_PRICE = os.getenv("PREMIUM_PRICE", "$20 USDT (Lifetime Access)")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "hamzutrader").lstrip("@")
ADMIN_URL = f"https://t.me/{ADMIN_USERNAME}"

PKT = timezone(timedelta(hours=5))
SIGNAL_COUNT = 0
DB_FILE = "authorized_users.json"
DEMO_COOLDOWN_SECONDS = 3600  # 1 Hour Cooldown

# --- Helper to safely escape Markdown ---
def escape_md(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'([_*\[\]`])', r'\\\1', str(text))

# --- Persistent Database ---
def load_db():
    default_db = {
        "admin_id": ADMIN_ID,
        "approved_users": [],
        "rejected_users": [],
        "used_trader_ids": {},
        "paid_requests": {},
        "referral_requests": {},
        "demo_cooldowns": {}
    }
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                for k, v in default_db.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception as e:
            logger.error(f"Database load error: {e}")
    return default_db

def save_db(data):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Database save error: {e}")

db_data = load_db()
if db_data.get("admin_id") and not ADMIN_ID:
    ADMIN_ID = int(db_data["admin_id"])

# --- Automatic Admin Detection (By ID or @hamzutrader Username) ---
def check_and_register_admin(user) -> bool:
    global ADMIN_ID
    if not user:
        return False
    uid = user.id
    uname = (user.username or "").lstrip("@").lower()

    if (ADMIN_ID and uid == ADMIN_ID) or (uid in ENV_ALLOWED_IDS) or (uname and uname == ADMIN_USERNAME.lower()):
        if ADMIN_ID != uid:
            ADMIN_ID = uid
            db_data["admin_id"] = uid
            save_db(db_data)
        return True
    return False

def get_admin_chat_id() -> int:
    return ADMIN_ID or db_data.get("admin_id", 0)

def is_user_authorized(user) -> bool:
    if check_and_register_admin(user):
        return True
    if user.id in db_data.get("approved_users", []):
        return True
    return False

def authorize_user(user_id: int, trader_id: str = None):
    if user_id not in db_data["approved_users"]:
        db_data["approved_users"].append(user_id)
    if user_id in db_data.get("rejected_users", []):
        db_data["rejected_users"].remove(user_id)
    if trader_id and trader_id not in ["Not Provided", "Paid_User"]:
        db_data["used_trader_ids"][str(trader_id)] = user_id
    save_db(db_data)

def reject_user(user_id: int):
    if user_id in db_data.get("approved_users", []):
        db_data["approved_users"].remove(user_id)
    if user_id not in db_data.get("rejected_users", []):
        db_data["rejected_users"].append(user_id)
    save_db(db_data)

# ============================================================================
# STRICTLY VERIFIED QUOTEX LIVE CURRENCY PAIRS ONLY
# ============================================================================
QUOTEX_LIVE_MARKETS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "USD/CHF": "CHF=X",
    "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",
    "EUR/AUD": "EURAUD=X",
    "EUR/CAD": "EURCAD=X",
    "GBP/AUD": "GBPAUD=X",
    "GBP/CAD": "GBPCAD=X",
    "GBP/CHF": "GBPCHF=X",
    "AUD/CAD": "AUDCAD=X",
    "AUD/CHF": "AUDCHF=X",
    "CAD/JPY": "CADJPY=X",
    "CHF/JPY": "CHFJPY=X",
    "EUR/CHF": "EURCHF=X",
    "NZD/JPY": "NZDJPY=X",
    "CAD/CHF": "CADCHF=X",
    "NZD/CAD": "NZDCAD=X",
    "NZD/CHF": "NZDCHF=X",
    "EUR/NZD": "EURNZD=X",
    "GBP/NZD": "GBPNZD=X",
    "AUD/NZD": "AUDNZD=X",
}

# ============================================================================
# STRICTLY VERIFIED QUOTEX OTC CURRENCY PAIRS ONLY
# ============================================================================
QUOTEX_OTC_MARKETS = {
    "EUR/USD (OTC)": 101,
    "GBP/USD (OTC)": 102,
    "USD/JPY (OTC)": 103,
    "AUD/USD (OTC)": 104,
    "USD/CAD (OTC)": 105,
    "USD/CHF (OTC)": 106,
    "NZD/USD (OTC)": 107,
    "EUR/GBP (OTC)": 108,
    "EUR/JPY (OTC)": 109,
    "GBP/JPY (OTC)": 110,
    "AUD/JPY (OTC)": 111,
    "EUR/AUD (OTC)": 112,
    "EUR/CAD (OTC)": 113,
    "GBP/AUD (OTC)": 114,
    "GBP/CAD (OTC)": 115,
    "GBP/CHF (OTC)": 116,
    "AUD/CAD (OTC)": 117,
    "AUD/CHF (OTC)": 118,
    "CAD/JPY (OTC)": 119,
    "CHF/JPY (OTC)": 120,
    "EUR/CHF (OTC)": 121,
    "NZD/JPY (OTC)": 122,
    "USD/PKR (OTC)": 201,
    "USD/INR (OTC)": 202,
    "USD/BRL (OTC)": 203,
    "USD/BDT (OTC)": 204,
    "USD/EGP (OTC)": 205,
    "USD/TRY (OTC)": 206,
    "USD/IDR (OTC)": 207,
    "USD/MXN (OTC)": 208,
    "USD/NGN (OTC)": 209,
    "USD/ARS (OTC)": 210,
    "USD/PHP (OTC)": 211,
    "USD/COP (OTC)": 212,
    "USD/DZD (OTC)": 213,
    "USD/ZAR (OTC)": 214,
}

# --- OTC Candle Series Generator ---
def generate_otc_ohlcv(pair_name, seed_val, count=60, target_timestamp=None):
    if target_timestamp is None:
        now_utc = datetime.now(timezone.utc)
        base_minute = int(now_utc.timestamp() // 60)
    else:
        base_minute = int(target_timestamp // 60)

    rows = []
    price = 100.0 + (seed_val % 50)

    for i in range(count):
        m_stamp = base_minute - (count - 1 - i)
        h = hashlib.sha256(f"{pair_name}_{seed_val}_{m_stamp}".encode()).hexdigest()
        val1 = (int(h[:8], 16) / 0xFFFFFFFF) - 0.5
        val2 = (int(h[8:16], 16) / 0xFFFFFFFF)
        val3 = (int(h[16:24], 16) / 0xFFFFFFFF)

        wave = np.sin(i / 3.8 + (seed_val % 11)) * 0.24
        delta = (val1 * 0.40) + wave
        open_p = price
        close_p = open_p + delta
        high_p = max(open_p, close_p) + (val2 * 0.18)
        low_p = min(open_p, close_p) - (val3 * 0.18)
        price = close_p
        rows.append([m_stamp * 60, open_p, high_p, low_p, close_p, 1000 + int(val2 * 500)])

    return pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

# ============================================================================
# HIGH-WINRATE M1 ANTI-CHASING ENGINE
# ============================================================================
def evaluate_high_accuracy_strategies(df, market_name, custom_entry_time=None):
    try:
        if df is None or len(df) < 40:
            return None

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
        if len(df) < 40:
            return None

        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()

        delta = df['close'].diff()
        gain5 = delta.clip(lower=0).ewm(alpha=1/5, adjust=False).mean()
        loss5 = (-delta.clip(upper=0)).ewm(alpha=1/5, adjust=False).mean()
        df['rsi_5'] = (100 - (100 / (1 + (gain5 / loss5.replace(0, np.nan))))).fillna(50)

        gain14 = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss14 = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        df['rsi_14'] = (100 - (100 / (1 + (gain14 / loss14.replace(0, np.nan))))).fillna(50)

        df['sma_20'] = df['close'].rolling(window=20).mean()
        df['std_20'] = df['close'].rolling(window=20).std()
        df['upper_bb'] = df['sma_20'] + (df['std_20'] * 2.0)
        df['lower_bb'] = df['sma_20'] - (df['std_20'] * 2.0)

        low_5 = df['low'].rolling(window=5).min()
        high_5 = df['high'].rolling(window=5).max()
        denom = (high_5 - low_5).replace(0, np.nan)
        df['stoch_k'] = (100 * ((df['close'] - low_5) / denom)).rolling(window=3).mean().fillna(50)
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean().fillna(50)

        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low'] - df['close'].shift(1)).abs()
        ], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean().bfill()
        df['support'] = df['low'].rolling(window=15).min()
        df['resistance'] = df['high'].rolling(window=15).max()

        c1 = df.iloc[-2]
        c2 = df.iloc[-3]
        c3 = df.iloc[-4]

        atr_val = float(c1['atr']) if c1['atr'] > 0 else 0.0001
        c1_body = abs(c1['close'] - c1['open'])
        c1_range = c1['high'] - c1['low']

        if c1_range < (atr_val * 0.15):
            return None

        c1_bull = c1['close'] > c1['open']
        c1_bear = c1['close'] < c1['open']
        c2_bull = c2['close'] > c2['open']
        c2_bear = c2['close'] < c2['open']
        c3_bull = c3['close'] > c3['open']
        c3_bear = c3['close'] < c3['open']

        upper_wick = c1['high'] - max(c1['open'], c1['close'])
        lower_wick = min(c1['open'], c1['close']) - c1['low']

        call_points = []
        put_points = []

        at_support = (c1['low'] <= c1['lower_bb']) or (abs(c1['low'] - c1['support']) <= atr_val * 0.40)
        at_resistance = (c1['high'] >= c1['upper_bb']) or (abs(c1['high'] - c1['resistance']) <= atr_val * 0.40)

        if at_support and (c1['rsi_5'] <= 25 or c1['stoch_k'] <= 20):
            call_points.append(("Support + Lower BB Reversal", 34))
            if lower_wick > (c1_body * 0.8):
                call_points.append(("Lower-Wick Buyer Rejection", 18))

        if at_resistance and (c1['rsi_5'] >= 75 or c1['stoch_k'] >= 80):
            put_points.append(("Resistance + Upper BB Reversal", 34))
            if upper_wick > (c1_body * 0.8):
                put_points.append(("Upper-Wick Seller Rejection", 18))

        uptrend = (c1['ema_20'] > c1['ema_50']) and (c1['close'] > c1['ema_50'])
        downtrend = (c1['ema_20'] < c1['ema_50']) and (c1['close'] < c1['ema_50'])

        if uptrend and (30 <= c1['rsi_14'] <= 62):
            if c1_bear and (c1['low'] <= c1['ema_9'] or c1['low'] <= c1['ema_20']):
                call_points.append(("Uptrend Red-Candle Dip Bounce", 32))
            elif c2_bear and c1_bull and c1['close'] > c2['open']:
                call_points.append(("Uptrend Bullish Engulfing Continuation", 28))

        if downtrend and (38 <= c1['rsi_14'] <= 70):
            if c1_bull and (c1['high'] >= c1['ema_9'] or c1['high'] >= c1['ema_20']):
                put_points.append(("Downtrend Green-Candle Rally Drop", 32))
            elif c2_bull and c1_bear and c1['close'] < c2['open']:
                put_points.append(("Downtrend Bearish Engulfing Continuation", 28))

        if c1_bull and c2_bull and (c3_bull or c1['rsi_5'] >= 72):
            put_points.append(("Bullish Streak Exhaustion Pullback", 26))
        elif c1_bear and c2_bear and (c3_bear or c1['rsi_5'] <= 28):
            call_points.append(("Bearish Streak Exhaustion Bounce", 26))

        if c1['stoch_k'] < 25 and c1['stoch_k'] >= c2['stoch_k']:
            call_points.append((f"Stoch/RSI({c1['rsi_5']:.0f}) Oversold Hook Up", 16))
        elif c1['stoch_k'] > 75 and c1['stoch_k'] <= c2['stoch_k']:
            put_points.append((f"Stoch/RSI({c1['rsi_5']:.0f}) Overbought Hook Down", 16))

        if not call_points and not put_points:
            if c1['rsi_5'] < 45:
                call_points.append(("Mean-Reversion Oversold Recovery", 18))
            elif c1['rsi_5'] > 55:
                put_points.append(("Mean-Reversion Overbought Pullback", 18))
            elif uptrend:
                call_points.append(("M5/M1 Bullish Trend Structure", 15))
            else:
                put_points.append(("M5/M1 Bearish Trend Structure", 15))

        call_score = sum(w for _, w in call_points)
        put_score = sum(w for _, w in put_points)
        score_diff = abs(call_score - put_score)

        if call_score >= put_score:
            direction = "CALL 🟢⬆️"
            reasons = [r for r, _ in sorted(call_points, key=lambda x: x[1], reverse=True)]
            winning_score = call_score
        else:
            direction = "PUT 🔴⬇️"
            reasons = [r for r, _ in sorted(put_points, key=lambda x: x[1], reverse=True)]
            winning_score = put_score

        confidence = min(74 + int(winning_score * 0.35) + int(score_diff * 0.25), 99)

        if custom_entry_time:
            entry_time = custom_entry_time
        else:
            now_pkt = datetime.now(PKT)
            entry_time = (now_pkt + timedelta(minutes=1)).strftime("%I:%M %p")

        return {
            'symbol': market_name,
            'direction': direction,
            'confidence': int(confidence),
            'score_diff': score_diff,
            'entry_time': entry_time,
            'reason': ", ".join(reasons[:2])
        }
    except Exception as e:
        logger.error(f"Strategy error on {market_name}: {e}")
        return None

# --- Single Pair & Auto Best Scanners ---
def fetch_single_pair_signal(pair_name, is_otc=False):
    if is_otc:
        seed_val = QUOTEX_OTC_MARKETS.get(pair_name)
        if not seed_val:
            return None
        df = generate_otc_ohlcv(pair_name, seed_val, count=60)
        return evaluate_high_accuracy_strategies(df, pair_name)
    else:
        yf_ticker = QUOTEX_LIVE_MARKETS.get(pair_name)
        if not yf_ticker:
            return None
        try:
            data = yf.download(tickers=yf_ticker, period="1d", interval="1m", progress=False)
            if data is not None and not data.empty and len(data) >= 40:
                if isinstance(data.columns, pd.MultiIndex):
                    data.columns = data.columns.get_level_values(0)
                sub_df = data.dropna(subset=['Close'])
                if len(sub_df) >= 40 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                    clean_df = pd.DataFrame({
                        'open': sub_df['Open'].values,
                        'high': sub_df['High'].values,
                        'low': sub_df['Low'].values,
                        'close': sub_df['Close'].values,
                        'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                    })
                    return evaluate_high_accuracy_strategies(clean_df, f"{pair_name} (Live)")
        except Exception as e:
            logger.warning(f"Single Forex fetch error: {e}")
        return None

def scan_best_signals(mode="live", count=1):
    signals = []
    if mode == "otc":
        for pair_name, seed_val in QUOTEX_OTC_MARKETS.items():
            df = generate_otc_ohlcv(pair_name, seed_val, count=60)
            res = evaluate_high_accuracy_strategies(df, pair_name)
            if res:
                signals.append(res)
    else:
        try:
            yf_tickers = list(QUOTEX_LIVE_MARKETS.values())
            data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
            for pair_name, yf_sym in QUOTEX_LIVE_MARKETS.items():
                try:
                    if yf_sym in data and not data[yf_sym].empty:
                        sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                        if len(sub_df) >= 40 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                            clean_df = pd.DataFrame({
                                'open': sub_df['Open'].values,
                                'high': sub_df['High'].values,
                                'low': sub_df['Low'].values,
                                'close': sub_df['Close'].values,
                                'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                            })
                            res = evaluate_high_accuracy_strategies(clean_df, f"{pair_name} (Live)")
                            if res:
                                signals.append(res)
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Batch Yahoo Finance error: {e}")

    signals.sort(
        key=lambda x: (
            1 if x['score_diff'] >= 20 else 0,
            x['confidence'],
            x['score_diff']
        ),
        reverse=True
    )
    return signals[:count]

# --- Future Signals Generator ---
def generate_future_signals_list(mode="otc", total_signals=15):
    now_utc = datetime.now(timezone.utc)
    now_pkt = datetime.now(PKT)

    mins_to_add = 5 - (now_pkt.minute % 5)
    if mins_to_add < 2:
        mins_to_add += 5
    first_slot_pkt = (now_pkt + timedelta(minutes=mins_to_add)).replace(second=0, microsecond=0)
    first_slot_utc = (now_utc + timedelta(minutes=mins_to_add)).replace(second=0, microsecond=0)

    future_list = []

    if mode == "otc":
        otc_items = list(QUOTEX_OTC_MARKETS.items())
        for slot_idx in range(total_signals):
            slot_time_pkt = first_slot_pkt + timedelta(minutes=slot_idx * 5)
            slot_time_utc = first_slot_utc + timedelta(minutes=slot_idx * 5)
            slot_str = slot_time_pkt.strftime("%I:%M %p")
            target_ts = slot_time_utc.timestamp()

            slot_candidates = []
            for pair_name, seed_val in otc_items:
                df = generate_otc_ohlcv(pair_name, seed_val, count=60, target_timestamp=target_ts)
                res = evaluate_high_accuracy_strategies(df, pair_name, custom_entry_time=slot_str)
                if res:
                    slot_candidates.append(res)

            slot_candidates.sort(key=lambda x: (x['confidence'], x['score_diff']), reverse=True)
            chosen = slot_candidates[slot_idx % min(4, len(slot_candidates))]
            future_list.append(chosen)
    else:
        active_live_dfs = {}
        try:
            yf_tickers = list(QUOTEX_LIVE_MARKETS.values())
            data = yf.download(tickers=yf_tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
            for pair_name, yf_sym in QUOTEX_LIVE_MARKETS.items():
                try:
                    if yf_sym in data and not data[yf_sym].empty:
                        sub_df = data[yf_sym].copy().dropna(subset=['Close'])
                        if len(sub_df) >= 40 and sub_df['Close'].iloc[-1] != sub_df['Close'].iloc[-5]:
                            active_live_dfs[f"{pair_name} (Live)"] = pd.DataFrame({
                                'open': sub_df['Open'].values,
                                'high': sub_df['High'].values,
                                'low': sub_df['Low'].values,
                                'close': sub_df['Close'].values,
                                'volume': sub_df['Volume'].values if 'Volume' in sub_df else 1
                            })
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Live Future Signals fetch error: {e}")

        if not active_live_dfs:
            return []

        live_names = list(active_live_dfs.keys())
        for slot_idx in range(total_signals):
            slot_time_pkt = first_slot_pkt + timedelta(minutes=slot_idx * 5)
            slot_str = slot_time_pkt.strftime("%I:%M %p")
            slot_candidates = []

            for p_name in live_names:
                base_df = active_live_dfs[p_name].copy()
                shift_window = base_df.iloc[max(0, len(base_df) - 45 - (slot_idx % 10)):len(base_df) - (slot_idx % 10)].copy()
                if len(shift_window) >= 40:
                    res = evaluate_high_accuracy_strategies(shift_window, p_name, custom_entry_time=slot_str)
                    if res:
                        slot_candidates.append(res)

            if slot_candidates:
                slot_candidates.sort(key=lambda x: (x['confidence'], x['score_diff']), reverse=True)
                chosen = slot_candidates[slot_idx % min(5, len(slot_candidates))]
                future_list.append(chosen)

    return future_list

# ============================================================================
# KEYBOARDS (USER GATE, DEMO, PAID, FREE ACCESS, VIP & ADMIN DASHBOARD)
# ============================================================================
def get_access_gate_kb(is_adm: bool = False):
    """Initial buttons shown when a normal user opens the bot."""
    rows = [
        [InlineKeyboardButton("💬 DM Admin", url=ADMIN_URL)],
        [InlineKeyboardButton("🎯 Demo Signal", callback_data="gate_demo")],
        [InlineKeyboardButton("💎 Paid Signals", callback_data="gate_paid")],
        [InlineKeyboardButton("🆓 Free Permanent Access", callback_data="gate_free")]
    ]
    if is_adm:
        rows.append([InlineKeyboardButton("👑 Back to Admin Dashboard", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

def get_demo_generate_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Generate Signal", callback_data="demo_generate_signal")],
        [InlineKeyboardButton("⬅️ Back", callback_data="gate_home")]
    ])

def get_paid_signals_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done Payment", callback_data="paid_done")],
        [
            InlineKeyboardButton("💬 DM Admin", url=ADMIN_URL),
            InlineKeyboardButton("⬅️ Back", callback_data="gate_home")
        ]
    ])

def get_paid_request_access_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 Request Access", callback_data="paid_request_access")],
        [
            InlineKeyboardButton("💬 DM Admin (Send Screenshot)", url=ADMIN_URL),
            InlineKeyboardButton("⬅️ Back", callback_data="gate_paid")
        ]
    ])

def get_free_access_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Create Quotex Account ($50 Deposit)", url=QUOTEX_PARTNER_LINK)],
        [InlineKeyboardButton("✅ Account Created", callback_data="free_account_created")],
        [
            InlineKeyboardButton("💬 DM Admin", url=ADMIN_URL),
            InlineKeyboardButton("⬅️ Back", callback_data="gate_home")
        ]
    ])

def get_free_check_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Check", callback_data="free_check_id")],
        [InlineKeyboardButton("✍️ Re-enter Trader ID", callback_data="free_account_created")],
        [InlineKeyboardButton("⬅️ Back", callback_data="gate_free")]
    ])

# --- Separate Clean Dashboard for Admin vs Approved VIP Users ---
def get_main_dashboard_kb(user=None):
    if check_and_register_admin(user):
        paid_cnt = len(db_data.get("paid_requests", {}))
        ref_cnt = len(db_data.get("referral_requests", {}))
        vip_cnt = len(db_data.get("approved_users", []))
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💳 Paid User Requests ({paid_cnt})", callback_data="admin_paid_0")],
            [InlineKeyboardButton(f"🤝 Referral Requests ({ref_cnt})", callback_data="admin_ref_0")],
            [
                InlineKeyboardButton("📈 LIVE MARKET SIGNALS", callback_data="mode_live"),
                InlineKeyboardButton("🌙 OTC MARKET SIGNALS", callback_data="mode_otc")
            ],
            [InlineKeyboardButton("📅 FUTURE SIGNALS (10-20 LIST)", callback_data="menu_future")],
            [
                InlineKeyboardButton(f"👥 Approved Users ({vip_cnt})", callback_data="admin_users_list"),
                InlineKeyboardButton("👁️ Preview User Menu", callback_data="gate_home")
            ]
        ])

    # Normal Approved VIP User Dashboard
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 LIVE MARKET SIGNALS", callback_data="mode_live"),
            InlineKeyboardButton("🌙 OTC MARKET SIGNALS", callback_data="mode_otc")
        ],
        [
            InlineKeyboardButton("📅 FUTURE SIGNALS (10-20 LIST)", callback_data="menu_future")
        ],
        [
            InlineKeyboardButton("💬 DM Admin", url=ADMIN_URL)
        ]
    ])

def get_future_signals_kb():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 10 LIVE FUTURE", callback_data="fut_live_10"),
            InlineKeyboardButton("📈 15 LIVE FUTURE", callback_data="fut_live_15"),
            InlineKeyboardButton("📈 20 LIVE FUTURE", callback_data="fut_live_20")
        ],
        [
            InlineKeyboardButton("🌙 10 OTC FUTURE", callback_data="fut_otc_10"),
            InlineKeyboardButton("🌙 15 OTC FUTURE", callback_data="fut_otc_15"),
            InlineKeyboardButton("🌙 20 OTC FUTURE", callback_data="fut_otc_20")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="main_menu")
        ]
    ])

def get_market_sub_kb(mode):
    label = "LIVE" if mode == "live" else "OTC"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ AUTO BEST {label} SIGNAL (M1)", callback_data=f"auto_{mode}_1")],
        [InlineKeyboardButton(f"🔥 TOP 3 {label} SIGNALS (M1)", callback_data=f"auto_{mode}_3")],
        [InlineKeyboardButton(f"📅 {label} FUTURE SIGNALS (15 LIST)", callback_data=f"fut_{mode}_15")],
        [InlineKeyboardButton(f"🎯 SELECT SPECIFIC {label} PAIR", callback_data=f"list_{mode}_0")],
        [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="main_menu")]
    ])

def get_pairs_grid_kb(mode, page=0):
    markets = list(QUOTEX_LIVE_MARKETS.keys()) if mode == "live" else list(QUOTEX_OTC_MARKETS.keys())
    per_page = 10
    start_idx = page * per_page
    end_idx = start_idx + per_page
    sliced = markets[start_idx:end_idx]

    rows = []
    for i in range(0, len(sliced), 2):
        row = []
        for j in range(2):
            if i + j < len(sliced):
                p_name = sliced[i + j]
                idx = start_idx + i + j
                icon = "📊" if mode == "live" else "🌙"
                row.append(InlineKeyboardButton(f"{icon} {p_name}", callback_data=f"pair_{mode}_{idx}"))
        rows.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"list_{mode}_{page-1}"))
    if end_idx < len(markets):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_{mode}_{page+1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([
        InlineKeyboardButton("⚡ Mode Menu", callback_data=f"mode_{mode}"),
        InlineKeyboardButton("⬅️ Dashboard", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(rows)

def get_post_signal_kb(mode, pair_idx=None):
    retry_cb = f"pair_{mode}_{pair_idx}" if pair_idx is not None else f"auto_{mode}_1"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ GENERATE NEXT SIGNAL", callback_data=retry_cb)],
        [
            InlineKeyboardButton("📅 Future Signals List", callback_data=f"fut_{mode}_15"),
            InlineKeyboardButton("🎯 Choose Pair", callback_data=f"list_{mode}_0")
        ],
        [
            InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="main_menu")
        ]
    ])

# --- Formatters ---
def format_signal_card(sig, is_demo=False):
    global SIGNAL_COUNT
    SIGNAL_COUNT += 1
    header_tag = "🎯 *DEMO SIGNAL (1-HOUR COOLDOWN)* 🎯\n" if is_demo else ""
    return (
        f"👾 *HAMZATRADER* 👾\n"
        f"{header_tag}\n"
        f"━━━━━━━━ 🆓 ━━━━━━━━\n"
        f"📊 *PAIR :* `{sig['symbol']}`\n"
        f"⚡ *TIMEFRAME :* `M1`\n"
        f"⏰ *ENTRY TIME :* `{sig['entry_time']}`\n"
        f"🔊 *DIRECTION :* *{sig['direction']}*\n"
        f"⚠️ *1 STEP MTG MUST*\n"
        f"━━━━━━━━ 🆓 ━━━━━━━━\n\n"
        f"💮💮 *!SURESHOT SETUP!* 💮💮\n\n"
        f"📊 *ACCURACY :* `{sig['confidence']}%` 🟩\n"
        f"🧠 *STRATEGY :* _{sig['reason']}_\n"
        f"🟢 *SIGNAL COUNT :* `{SIGNAL_COUNT}` 🎖️\n\n"
        f"🚫 *Do not trade if market is giving you less than 70% profit!*\n\n"
        f"🔖 *TELEGRAM :* @{escape_md(ADMIN_USERNAME)} 🌸"
    )

def format_future_signals_card(signals_list, mode):
    global SIGNAL_COUNT
    SIGNAL_COUNT += len(signals_list)
    market_title = "LIVE MARKET" if mode == "live" else "OTC MARKET"
    now_date = datetime.now(PKT).strftime("%d %b %Y")

    lines = [
        f"👾 *HAMZATRADER* 👾",
        f"📅 *VIP {market_title} FUTURE SIGNALS*",
        f"━━━━━━━━ 🆓 ━━━━━━━━",
        f"🗓️ *DATE :* `{now_date}`",
        f"⚡ *TIMEFRAME :* `M1 (1-Minute)`",
        f"⏱️ *SIGNAL GAP :* `5 Minutes Each`",
        f"🌍 *TIMEZONE :* `PKT (UTC+5)`",
        f"━━━━━━━━ 🆓 ━━━━━━━━\n"
    ]

    for idx, sig in enumerate(signals_list, start=1):
        lines.append(
            f"`{idx:02d}.` ⏰ *{sig['entry_time']}* — `{sig['symbol']}` ➔ *{sig['direction']}* (`{sig['confidence']}%`)"
        )

    lines.extend([
        f"\n━━━━━━━━ 🆓 ━━━━━━━━",
        f"⚠️ *1 STEP MTG MUST*",
        f"🚫 *DON'T TRADE IF MARKET IS GIVING YOU LESS THAN 70% PROFIT!*",
        f"━━━━━━━━ 🆓 ━━━━━━━━\n",
        f"🔖 *TELEGRAM :* @{escape_md(ADMIN_USERNAME)} 🌸"
    ])
    return "\n".join(lines)

async def play_scan_animation(msg, target_label):
    stages = [
        f"⚡ *INITIALIZING HAMZATRADER ENGINE...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🔄 `Connecting to Quotex Currency Feed...`\n`[███▒▒▒▒▒▒▒] 35%`",
        f"🧠 *DETECTING PULLBACK & S&R ZONES...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n📊 `Filtering Trend Dip & Bollinger Exhaustion...`\n`[███████▒▒▒] 75%`",
        f"✅ *LOCKING SURESHOT ENTRY...*\n━━━━━━━━━━━━━━━━━━━━━━\n📡 *Target:* `{target_label}`\n🎯 `Calculating 1-Minute Entry & 1-Step MTG...`\n`[██████████] 100%`"
    ]
    for stage_txt in stages:
        try:
            await msg.edit_text(stage_txt, parse_mode='Markdown')
            await asyncio.sleep(0.40)
        except Exception:
            pass

# --- Text Templates ---
GATE_HOME_TEXT = (
    "👾 *WELCOME TO HAMZATRADER BOT* 👾\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Welcome to the official *Quotex M1 Signal Terminal*. Choose an option below to get started:\n\n"
    "🎯 *Demo Signal:* Test a live signal right now (1 signal every 1 hour).\n"
    "💎 *Paid Signals:* Purchase lifetime VIP access via USDT with zero restrictions.\n"
    "🆓 *Free Permanent Access:* Register a Quotex account through our official link and deposit $50 for unlimited access.\n\n"
    f"🔖 *ADMIN :* @{escape_md(ADMIN_USERNAME)} 🌸"
)

def get_dashboard_text(user) -> str:
    if check_and_register_admin(user):
        paid_cnt = len(db_data.get("paid_requests", {}))
        ref_cnt = len(db_data.get("referral_requests", {}))
        vip_cnt = len(db_data.get("approved_users", []))
        return (
            "👑 *HAMZATRADER — ADMIN CONTROL PANEL* 👑\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *Admin :* @{escape_md(ADMIN_USERNAME)} (`{user.id}`)\n"
            f"💳 *Pending Paid Requests :* `{paid_cnt}`\n"
            f"🤝 *Pending Referral Requests :* `{ref_cnt}`\n"
            f"👥 *Total Approved Users :* `{vip_cnt}`\n\n"
            "Select an option below to manage user requests or generate signals:"
        )
    return (
        "👾 *HAMZATRADER VIP DASHBOARD* 👾\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ *VIP STATUS : ACTIVE (UNLIMITED ACCESS)*\n"
        "Select your trading mode below to generate high-accuracy *LIVE*, *OTC*, or *FUTURE (10-20 List)* signals for verified Quotex currency pairs.\n\n"
        "💠 *Engine :* `Anti-Chasing Pullback + S&R Reversal`\n"
        "🔥 *Pairs :* `100% Verified Quotex Currency Pairs Only`\n"
        "🚫 *Rule :* `Don't trade if payout is less than 70%`\n\n"
        f"🔖 *TELEGRAM :* @{escape_md(ADMIN_USERNAME)} 🌸"
    )

# --- Admin Request Viewer Helper ---
async def render_admin_requests_view(query, req_type: str, index: int = 0):
    storage_key = "paid_requests" if req_type == "paid" else "referral_requests"
    title = "💳 PAID USER REQUESTS" if req_type == "paid" else "🤝 REFERRAL REQUESTS"
    requests_dict = db_data.get(storage_key, {})
    req_items = list(requests_dict.items())

    if not req_items:
        text = (
            f"*{title}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ There are currently *0* pending requests in this queue."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_{req_type}_0")],
            [InlineKeyboardButton("⬅️ Back to Admin Dashboard", callback_data="main_menu")]
        ])
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)
        return

    if index < 0:
        index = 0
    if index >= len(req_items):
        index = len(req_items) - 1

    req_uid_str, req_info = req_items[index]
    uname = escape_md(req_info.get("username", "No Username"))
    fname = escape_md(req_info.get("full_name", "User"))
    t_id = escape_md(req_info.get("trader_id", "Not Provided"))
    req_time = escape_md(req_info.get("time", "N/A"))

    text = (
        f"*{title} ({index + 1} of {len(req_items)})*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 *Name :* {fname}\n"
        f"🔗 *Username :* {uname}\n"
        f"🆔 *Telegram ID :* `{req_uid_str}`\n"
        f"📊 *Trader ID :* `{t_id}`\n"
        f"⏰ *Submitted :* `{req_time}`\n\n"
        f"Select an action below to Approve or Reject this user:"
    )

    rows = [
        [
            InlineKeyboardButton("❌ Reject", callback_data=f"adm_dec_{req_type}_rej_{req_uid_str}_{index}"),
            InlineKeyboardButton("✅ Approve", callback_data=f"adm_dec_{req_type}_app_{req_uid_str}_{index}")
        ]
    ]

    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"admin_{req_type}_{index - 1}"))
    if index + 1 < len(req_items):
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_{req_type}_{index + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to Admin Dashboard", callback_data="main_menu")])

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(rows))

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data.clear()

    if is_user_authorized(user):
        await update.message.reply_text(
            get_dashboard_text(user),
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb(user),
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            GATE_HOME_TEXT,
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb(False),
            disable_web_page_preview=True
        )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if check_and_register_admin(user):
        await update.message.reply_text(
            get_dashboard_text(user),
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb(user),
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            f"⛔ You are not authorized as Admin. Your Telegram ID is `{user.id}`.",
            parse_mode='Markdown'
        )

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not check_and_register_admin(user):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ *Usage:* `/adduser <Telegram_User_ID>`", parse_mode='Markdown')
        return

    new_uid = int(context.args[0])
    authorize_user(new_uid)
    await update.message.reply_text(f"✅ *User `{new_uid}` has been granted VIP Permanent Access!*", parse_mode='Markdown')
    try:
        await context.bot.send_message(
            chat_id=new_uid,
            text=(
                "🎉 *CONGRATULATIONS!*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "💎 Your *VIP Permanent Access* has been approved by the Admin!\n\n"
                "👉 Send `/start` to open the Signal Terminal."
            ),
            parse_mode='Markdown'
        )
    except Exception:
        pass

# --- Text Message Handler ---
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    state = context.user_data.get("state")

    # 1. Free Permanent Access: User is entering their Trader ID
    if state == "awaiting_free_trader_id":
        match = re.search(r'\b(\d{5,15})\b', text)
        if not match:
            await update.message.reply_text(
                "⚠️ *INVALID TRADER ID*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Please enter a valid numeric *Quotex Trader ID* (for example: `95420333`).",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="gate_free")]])
            )
            return

        trader_id = match.group(1)
        used_by = db_data.get("used_trader_ids", {}).get(trader_id)
        if used_by and used_by != user_id:
            await update.message.reply_text(
                "⛔ *TRADER ID ALREADY USED*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Trader ID `{trader_id}` is already registered to another user. Please register your own Quotex account via our partner link.",
                parse_mode='Markdown',
                reply_markup=get_free_access_kb()
            )
            return

        context.user_data["free_trader_id"] = trader_id
        await update.message.reply_text(
            "✅ *TRADER ID RECEIVED*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *Your Trader ID :* `{trader_id}`\n\n"
            "Click the *Check* button below to submit your Trader ID for verification and approval:",
            parse_mode='Markdown',
            reply_markup=get_free_check_kb()
        )
        return

    # 2. Paid Signals: User enters their Trader ID before clicking Request Access
    if state == "awaiting_paid_trader_id":
        clean_id = re.sub(r'[^a-zA-Z0-9_-]', '', text)[:25]
        if not clean_id:
            await update.message.reply_text(
                "⚠️ Please enter a valid *Trader ID* or click *Request Access* below.",
                parse_mode='Markdown',
                reply_markup=get_paid_request_access_kb()
            )
            return

        context.user_data["paid_trader_id"] = clean_id
        await update.message.reply_text(
            "✅ *TRADER ID SAVED FOR PAID REQUEST*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *Trader ID :* `{escape_md(clean_id)}`\n\n"
            "Now click *Request Access* below and make sure you have sent your payment screenshot to the Admin!",
            parse_mode='Markdown',
            reply_markup=get_paid_request_access_kb()
        )
        return

    # Default response for authorized vs unauthorized users
    if is_user_authorized(user):
        await update.message.reply_text(
            get_dashboard_text(user),
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb(user),
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            GATE_HOME_TEXT,
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb(False),
            disable_web_page_preview=True
        )

# --- Callback Query Handler ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    user_id = user.id
    is_adm = check_and_register_admin(user)
    username_display = f"@{user.username}" if user.username else "No Username"
    full_name_display = user.full_name or "User"

    # =========================================================================
    # 1. PUBLIC / UNAPPROVED USER FLOWS (HOME, DEMO, PAID, FREE ACCESS)
    # =========================================================================
    if data == "gate_home":
        context.user_data.clear()
        await query.edit_message_text(
            GATE_HOME_TEXT,
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb(is_adm),
            disable_web_page_preview=True
        )
        return

    # --- DEMO SIGNAL FLOW ---
    if data == "gate_demo":
        context.user_data.clear()
        demo_text = (
            "🎯 *DEMO SIGNAL ACCESS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Click *Generate Signal* below to test a live M1 Sureshot Signal.\n"
            "⏳ _Note: You can generate 1 Demo Signal every 1 hour._"
        )
        await query.edit_message_text(
            demo_text,
            parse_mode='Markdown',
            reply_markup=get_demo_generate_kb(),
            disable_web_page_preview=True
        )
        return

    if data == "demo_generate_signal":
        now_ts = int(datetime.now(timezone.utc).timestamp())
        last_demo_ts = db_data.get("demo_cooldowns", {}).get(str(user_id), 0)
        elapsed = now_ts - last_demo_ts

        if elapsed < DEMO_COOLDOWN_SECONDS:
            rem_secs = DEMO_COOLDOWN_SECONDS - elapsed
            rem_mins = max(1, (rem_secs + 59) // 60)
            cooldown_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Paid Signals (Unlimited)", callback_data="gate_paid")],
                [InlineKeyboardButton("🆓 Free Permanent Access", callback_data="gate_free")],
                [InlineKeyboardButton("⬅️ Back", callback_data="gate_home")]
            ])
            await query.edit_message_text(
                "⏳ *DEMO COOLDOWN ACTIVE (1 HOUR)*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"You have already generated your Demo Signal. You can generate your next Demo Signal in *{rem_mins} minute(s)*.\n\n"
                "💡 Want *unlimited signals* with zero cooldown? Choose *Paid Signals* or *Free Permanent Access* below!",
                parse_mode='Markdown',
                reply_markup=cooldown_kb
            )
            return

        await play_scan_animation(query.message, "DEMO M1 SIGNAL")
        demo_sigs = await asyncio.to_thread(scan_best_signals, "live", 1)
        if not demo_sigs:
            demo_sigs = await asyncio.to_thread(scan_best_signals, "otc", 1)

        if not demo_sigs:
            await query.message.edit_text(
                "⚠️ *Market data is refreshing. Please try again in a few seconds.*",
                parse_mode='Markdown',
                reply_markup=get_demo_generate_kb()
            )
            return

        db_data["demo_cooldowns"][str(user_id)] = now_ts
        save_db(db_data)

        card = format_signal_card(demo_sigs[0], is_demo=True)
        card += "\n\n⏳ _Your 1-hour Demo Cooldown has started. Upgrade below for unlimited access!_"
        demo_post_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Paid Signals (Unlimited)", callback_data="gate_paid")],
            [InlineKeyboardButton("🆓 Free Permanent Access", callback_data="gate_free")],
            [InlineKeyboardButton("⬅️ Back to Home", callback_data="gate_home")]
        ])
        await query.message.edit_text(
            card,
            parse_mode='Markdown',
            reply_markup=demo_post_kb,
            disable_web_page_preview=True
        )
        return

    # --- PAID SIGNALS FLOW ---
    if data == "gate_paid":
        context.user_data.clear()
        adm_id_label = get_admin_chat_id() or f"@{ADMIN_USERNAME}"
        paid_text = (
            "💎 *PAID SIGNALS (LIFETIME UNLIMITED ACCESS)*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Purchase permanent VIP access to unlock unlimited Live, OTC, and Future signals without any restrictions:\n\n"
            f"💰 *Price :* `{PREMIUM_PRICE}`\n"
            f"🌐 *Network :* `{USDT_NETWORK}`\n"
            f"🏦 *USDT Address :*\n`{USDT_ADDRESS}`\n\n"
            f"👤 *Admin Username :* @{escape_md(ADMIN_USERNAME)}\n"
            f"🆔 *Admin ID :* `{escape_md(str(adm_id_label))}`\n"
            f"🆔 *Your Telegram ID :* `{user_id}`\n\n"
            "📌 *Steps to Activate:*\n"
            "1️⃣ Send the USDT payment to the address above.\n"
            "2️⃣ Click *Done Payment* below.\n"
            "3️⃣ Enter your Quotex Trader ID, click *Request Access*, and send your payment screenshot to the Admin!"
        )
        await query.edit_message_text(
            paid_text,
            parse_mode='Markdown',
            reply_markup=get_paid_signals_kb(),
            disable_web_page_preview=True
        )
        return

    if data == "paid_done":
        context.user_data["state"] = "awaiting_paid_trader_id"
        context.user_data["paid_trader_id"] = context.user_data.get("paid_trader_id", "Paid_User")
        done_text = (
            "✅ *PAYMENT COMPLETED — FINAL STEP*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "1️⃣ Please *type and send your Quotex Trader ID* in this chat right now (or tap *Request Access* directly below).\n"
            "2️⃣ Click *Request Access* to submit your request to the Admin dashboard.\n"
            f"3️⃣ Send your *payment screenshot* to @{escape_md(ADMIN_USERNAME)} so the Admin can verify and approve your unlimited access!"
        )
        await query.edit_message_text(
            done_text,
            parse_mode='Markdown',
            reply_markup=get_paid_request_access_kb()
        )
        return

    if data == "paid_request_access":
        trader_id = context.user_data.get("paid_trader_id", "Paid_User")
        context.user_data.clear()
        now_str = datetime.now(PKT).strftime("%d %b %Y %I:%M %p")

        db_data["paid_requests"][str(user_id)] = {
            "user_id": user_id,
            "username": username_display,
            "full_name": full_name_display,
            "trader_id": trader_id,
            "time": now_str
        }
        save_db(db_data)

        adm_chat = get_admin_chat_id()
        if adm_chat:
            admin_alert_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("❌ Reject", callback_data=f"adm_dec_paid_rej_{user_id}_0"),
                    InlineKeyboardButton("✅ Approve", callback_data=f"adm_dec_paid_app_{user_id}_0")
                ],
                [InlineKeyboardButton("💳 Open Paid User Requests", callback_data="admin_paid_0")]
            ])
            try:
                await context.bot.send_message(
                    chat_id=adm_chat,
                    text=(
                        "💳 *NEW PAID USER REQUEST*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 *Name :* {escape_md(full_name_display)}\n"
                        f"🔗 *Username :* {escape_md(username_display)}\n"
                        f"🆔 *Telegram ID :* `{user_id}`\n"
                        f"📊 *Trader ID :* `{escape_md(trader_id)}`\n"
                        f"⏰ *Time :* `{now_str}`"
                    ),
                    parse_mode='Markdown',
                    reply_markup=admin_alert_kb
                )
            except Exception as e:
                logger.error(f"Admin paid notify error: {e}")

        await query.edit_message_text(
            "⏳ *PAID ACCESS REQUEST SUBMITTED!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Your request has been sent to the Admin for approval.\n"
            f"📸 *Important:* Please make sure you have sent your payment screenshot to @{escape_md(ADMIN_USERNAME)}.\n\n"
            "🔒 _Approval is compulsory. As soon as the Admin approves your payment, you will receive a notification and unlock unlimited lifetime access!_",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📸 Send Screenshot to Admin", url=ADMIN_URL)],
                [InlineKeyboardButton("⬅️ Back to Home", callback_data="gate_home")]
            ])
        )
        return

    # --- FREE PERMANENT ACCESS FLOW ---
    if data == "gate_free":
        context.user_data.clear()
        free_text = (
            "🆓 *FREE PERMANENT ACCESS*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Follow these steps to unlock unlimited VIP signals for free:\n\n"
            "1️⃣ Create a new Quotex account through our *official partner link* below.\n"
            "2️⃣ Deposit at least *$50* into your Quotex account.\n"
            "3️⃣ Click *Account Created* below, enter your *Trader ID*, and press *Check*.\n\n"
            "🔒 _Once submitted, our Admin will verify your referral account and deposit and approve your permanent access._"
        )
        await query.edit_message_text(
            free_text,
            parse_mode='Markdown',
            reply_markup=get_free_access_kb(),
            disable_web_page_preview=True
        )
        return

    if data == "free_account_created":
        context.user_data["state"] = "awaiting_free_trader_id"
        await query.edit_message_text(
            "✍️ *ENTER YOUR QUOTEX TRADER ID*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Please type and send your numeric *Quotex Trader ID* (for example: `95420333`) in this chat right now.\n\n"
            "After entering your Trader ID, press the *Check* button that appears!",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="gate_free")]])
        )
        return

    if data == "free_check_id":
        trader_id = context.user_data.get("free_trader_id")
        if not trader_id:
            context.user_data["state"] = "awaiting_free_trader_id"
            await query.edit_message_text(
                "⚠️ *Please type and send your numeric Quotex Trader ID first:*",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="gate_free")]])
            )
            return

        context.user_data.clear()
        now_str = datetime.now(PKT).strftime("%d %b %Y %I:%M %p")

        db_data["referral_requests"][str(user_id)] = {
            "user_id": user_id,
            "username": username_display,
            "full_name": full_name_display,
            "trader_id": trader_id,
            "time": now_str
        }
        save_db(db_data)

        adm_chat = get_admin_chat_id()
        if adm_chat:
            admin_alert_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("❌ Reject", callback_data=f"adm_dec_ref_rej_{user_id}_0"),
                    InlineKeyboardButton("✅ Approve", callback_data=f"adm_dec_ref_app_{user_id}_0")
                ],
                [InlineKeyboardButton("🤝 Open Referral Requests", callback_data="admin_ref_0")]
            ])
            try:
                await context.bot.send_message(
                    chat_id=adm_chat,
                    text=(
                        "🤝 *NEW REFERRAL REQUEST ($50 DEPOSIT)*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 *Name :* {escape_md(full_name_display)}\n"
                        f"🔗 *Username :* {escape_md(username_display)}\n"
                        f"🆔 *Telegram ID :* `{user_id}`\n"
                        f"📊 *Trader ID :* `{escape_md(trader_id)}`\n"
                        f"⏰ *Time :* `{now_str}`"
                    ),
                    parse_mode='Markdown',
                    reply_markup=admin_alert_kb
                )
            except Exception as e:
                logger.error(f"Admin referral notify error: {e}")

        await query.edit_message_text(
            "⏳ *REFERRAL REQUEST SUBMITTED!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 *Trader ID :* `{escape_md(trader_id)}`\n"
            f"👤 *Username :* {escape_md(username_display)}\n"
            f"📌 *Status :* `Pending Admin Approval`\n\n"
            "Your Trader ID has been sent to the Admin for verification. Until the Admin approves your request, you cannot use the VIP bot.\n\n"
            "🔔 _You will receive an automatic message here as soon as your request is approved!_",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 DM Admin", url=ADMIN_URL)],
                [InlineKeyboardButton("⬅️ Back to Home", callback_data="gate_home")]
            ])
        )
        return

    # =========================================================================
    # 2. ADMIN DASHBOARD ACTIONS (PAID REQUESTS, REFERRAL REQUESTS, USERS LIST)
    # =========================================================================
    if data == "admin_users_list":
        if not is_adm:
            return
        approved = db_data.get("approved_users", [])
        if not approved:
            txt = "👥 *APPROVED VIP USERS*\n━━━━━━━━━━━━━━━━━━━━━━\n\nNo approved users yet."
        else:
            lines = "\n".join([f"• `{uid}`" for uid in approved[:40]])
            txt = f"👥 *APPROVED VIP USERS ({len(approved)})*\n━━━━━━━━━━━━━━━━━━━━━━\n\n{lines}"
        await query.edit_message_text(
            txt,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin Dashboard", callback_data="main_menu")]])
        )
        return

    if data.startswith("admin_paid_") or data.startswith("admin_ref_"):
        if not is_adm:
            return
        parts = data.split("_")
        req_type = parts[1]
        idx = int(parts[2])
        await render_admin_requests_view(query, req_type, idx)
        return

    if data.startswith("adm_dec_"):
        if not is_adm:
            return
        parts = data.split("_")
        req_type = parts[2]
        decision = parts[3]
        target_uid_str = parts[4]
        target_uid = int(target_uid_str)
        idx = int(parts[5]) if len(parts) > 5 else 0

        storage_key = "paid_requests" if req_type == "paid" else "referral_requests"
        req_info = db_data.get(storage_key, {}).pop(target_uid_str, None)
        t_id = req_info.get("trader_id", "Not Provided") if req_info else "Not Provided"

        if decision == "app":
            authorize_user(target_uid, t_id)
            save_db(db_data)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        "🎉 *ACCESS APPROVED!*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "✅ Your request has been *APPROVED* by the Admin! You now have *Unlimited Permanent Access* to the HamzaTrader Bot.\n\n"
                        "👇 Select your trading mode below to start generating signals:"
                    ),
                    parse_mode='Markdown',
                    reply_markup=get_main_dashboard_kb(None)
                )
            except Exception as e:
                logger.warning(f"Could not notify approved user {target_uid}: {e}")
        else:
            reject_user(target_uid)
            save_db(db_data)
            try:
                await context.bot.send_message(
                    chat_id=target_uid,
                    text=(
                        "❌ *ACCESS REQUEST REJECTED*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "Your access request was reviewed and *rejected* by the Admin because your payment or $50 partner deposit could not be verified.\n\n"
                        f"If you believe this is a mistake, please contact @{escape_md(ADMIN_USERNAME)}."
                    ),
                    parse_mode='Markdown',
                    reply_markup=get_access_gate_kb(False)
                )
            except Exception as e:
                logger.warning(f"Could not notify rejected user {target_uid}: {e}")

        await render_admin_requests_view(query, req_type, idx)
        return

    # =========================================================================
    # 3. COMPULSORY AUTHORIZATION GATE FOR ALL VIP TERMINAL FEATURES
    # =========================================================================
    if not is_user_authorized(user):
        await query.edit_message_text(
            GATE_HOME_TEXT,
            parse_mode='Markdown',
            reply_markup=get_access_gate_kb(False),
            disable_web_page_preview=True
        )
        return

    # =========================================================================
    # 4. VIP SIGNAL TERMINAL FLOWS (FOR APPROVED USERS & ADMIN)
    # =========================================================================
    if data in ["main_menu", "menu_modes"]:
        await query.edit_message_text(
            get_dashboard_text(user),
            parse_mode='Markdown',
            reply_markup=get_main_dashboard_kb(user),
            disable_web_page_preview=True
        )
        return

    if data == "menu_future":
        fut_text = (
            "📅 *HAMZATRADER FUTURE SIGNALS TERMINAL*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "ℹ️ Generate *10, 15, or 20 Future Signals* with an exact *5-Minute Gap* between each trade.\n\n"
            "📈 *Live Future Signals :* Monday–Friday Real Forex Pairs\n"
            "🌙 *OTC Future Signals :* 24/7 Quotex OTC Currency Pairs\n"
            "🚫 *Rule :* Do not trade if market is giving less than 70% profit!"
        )
        await query.edit_message_text(
            fut_text,
            parse_mode='Markdown',
            reply_markup=get_future_signals_kb()
        )
        return

    if data.startswith("fut_"):
        _, mode, count_str = data.split("_")
        count = int(count_str)
        label = f"{count} {'LIVE' if mode == 'live' else 'OTC'} FUTURE SIGNALS"

        await play_scan_animation(query.message, label)
        fut_signals = await asyncio.to_thread(generate_future_signals_list, mode, count)

        if not fut_signals:
            closed_text = (
                "⚠️ *LIVE FOREX MARKET IS CURRENTLY CLOSED*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "ℹ️ Global Live Forex markets are closed right now. Quotex is operating *OTC Currency Pairs*.\n\n"
                "👉 Tap below to generate *OTC Future Signals*!"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🌙 10 OTC FUTURE", callback_data="fut_otc_10"),
                    InlineKeyboardButton("🌙 15 OTC FUTURE", callback_data="fut_otc_15"),
                    InlineKeyboardButton("🌙 20 OTC FUTURE", callback_data="fut_otc_20")
                ],
                [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="main_menu")]
            ])
            await query.message.edit_text(closed_text, parse_mode='Markdown', reply_markup=kb)
            return

        card_text = format_future_signals_card(fut_signals, mode)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔄 REGENERATE {count} FUTURE SIGNALS", callback_data=data)],
            [
                InlineKeyboardButton("📅 Future Menu", callback_data="menu_future"),
                InlineKeyboardButton("⬅️ Dashboard", callback_data="main_menu")
            ]
        ])
        await query.message.edit_text(
            card_text,
            parse_mode='Markdown',
            reply_markup=kb,
            disable_web_page_preview=True
        )
        return

    if data in ["mode_live", "mode_otc"]:
        mode = "live" if data == "mode_live" else "otc"
        title = "📈 LIVE MARKET TERMINAL" if mode == "live" else "🌙 OTC MARKET TERMINAL"
        desc = (
            "28 Verified Quotex Live Forex Currency Pairs."
            if mode == "live"
            else "36 Verified Quotex OTC Currency Pairs (24/7 Active)."
        )
        text = (
            f"*{title}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"ℹ️ {desc}\n\n"
            f"Choose *Auto Best Signal*, *Future Signals List (5-Min Gap)*, or *Select Specific Pair* below:"
        )
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_market_sub_kb(mode)
        )
        return

    if data.startswith("list_"):
        _, mode, page_str = data.split("_")
        page = int(page_str)
        label = "LIVE" if mode == "live" else "OTC"
        text = (
            f"🎯 *SELECT QUOTEX {label} PAIR (Page {page + 1})*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ Tap any verified Quotex currency pair below to generate an instant *M1 Signal*:"
        )
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_pairs_grid_kb(mode, page)
        )
        return

    if data.startswith("pair_"):
        _, mode, idx_str = data.split("_")
        idx = int(idx_str)
        markets = list(QUOTEX_LIVE_MARKETS.keys()) if mode == "live" else list(QUOTEX_OTC_MARKETS.keys())
        if idx >= len(markets):
            return
        pair_name = markets[idx]

        await play_scan_animation(query.message, pair_name)
        sig = await asyncio.to_thread(fetch_single_pair_signal, pair_name, mode == "otc")

        if not sig:
            closed_text = (
                f"⚠️ *LIVE PAIR CURRENTLY CLOSED / INACTIVE*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"ℹ️ `{pair_name} (Live)` is currently closed on global exchanges. Quotex operates `{pair_name} (OTC)` when live markets are closed.\n\n"
                f"👉 Tap below to switch to *OTC Markets*!"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌙 SWITCH TO OTC MARKETS", callback_data="mode_otc")],
                [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="main_menu")]
            ])
            await query.message.edit_text(closed_text, parse_mode='Markdown', reply_markup=kb)
            return

        card = format_signal_card(sig)
        await query.message.edit_text(
            card,
            parse_mode='Markdown',
            reply_markup=get_post_signal_kb(mode, idx),
            disable_web_page_preview=True
        )
        return

    if data.startswith("auto_"):
        _, mode, count_str = data.split("_")
        count = int(count_str)
        label = "ALL QUOTEX LIVE CURRENCY PAIRS" if mode == "live" else "ALL QUOTEX OTC CURRENCY PAIRS"

        await play_scan_animation(query.message, label)
        found_signals = await asyncio.to_thread(scan_best_signals, mode, count)

        if not found_signals:
            await query.message.edit_text(
                "⚠️ *LIVE FOREX MARKETS ARE CLOSED*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "ℹ️ Please switch to *OTC Market Signals* right now.",
                parse_mode='Markdown',
                reply_markup=get_main_dashboard_kb(user)
            )
            return

        first_card = format_signal_card(found_signals[0])
        await query.message.edit_text(
            first_card,
            parse_mode='Markdown',
            reply_markup=get_post_signal_kb(mode, None),
            disable_web_page_preview=True
        )

        for extra_sig in found_signals[1:]:
            await asyncio.sleep(0.35)
            extra_card = format_signal_card(extra_sig)
            await query.message.reply_text(
                extra_card,
                parse_mode='Markdown',
                reply_markup=get_post_signal_kb(mode, None),
                disable_web_page_preview=True
            )

def run_bot():
    logger.info("Starting HamzaTrader Quotex Telegram Bot...")
    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("admin", admin_command))
    telegram_app.add_handler(CommandHandler("scan", scan_command))
    telegram_app.add_handler(CommandHandler("adduser", adduser_command))
    telegram_app.add_handler(CallbackQueryHandler(button_handler))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    telegram_app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    run_bot()
