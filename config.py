"""
Config for the "Edge 1st" bot — the FIRST Edge strategy (archived
strategy_edge.py, "Strategy 5"), the one whose 2022-2026 walk-forward on the
1-minute-exit engine returned +Rs 5,67,297 (NIFTY) / +Rs 7,51,154 (BANKNIFTY)
net of full Zerodha F&O costs.

These are NSE index-FUTURES paper-trading risk rules (points x lot_size
payoff). No real orders are ever placed.
"""

# --- instruments ---
INSTRUMENTS = ["NIFTY", "BANKNIFTY"]
LOT_SIZES = {"NIFTY": 25, "BANKNIFTY": 15}

# --- market data source: "upstox" (needs a daily login) or "yahoo" (free) ---
# Override at runtime with env var EDGE1ST_DATA=yahoo|upstox
DATA_SOURCE = "upstox"

# Yahoo (fallback / free): tickers, ~15 min delayed, 7-day 1-min cap
YAHOO_SYMBOLS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}

# Upstox: index instrument keys (clean OHLC, no futures needed)
UPSTOX_INSTRUMENT_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
}
# Your Upstox developer-app credentials. Values from the process environment
# take precedence; otherwise load them from this folder's private .env file.
import os as _os
from pathlib import Path as _Path

_env_file = _Path(__file__).with_name(".env")
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _value = _line.split("=", 1)
        _os.environ.setdefault(_key.strip(), _value.strip().strip('"').strip("'"))

UPSTOX_API_KEY = _os.getenv("UPSTOX_API_KEY", "PUT_YOUR_UPSTOX_API_KEY_HERE")
UPSTOX_API_SECRET = _os.getenv("UPSTOX_API_SECRET", "PUT_YOUR_UPSTOX_API_SECRET_HERE")
UPSTOX_REDIRECT_URI = _os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8888/callback")

# --- timeframes ---
ENTRY_TIMEFRAME_MIN = 5        # entry evaluation + fill grain
REFERENCE_TIMEFRAME_MIN = 10   # trend / regime grain

# --- risk / trade management ---
RISK_REWARD_RATIO = 3.0        # target = 3x stop distance (strategy uses ATR/swing stop)
STOP_LOSS_POINTS = 50          # only a fallback; strategy.compute_stop_and_target overrides
TAKE_PROFIT_POINTS = STOP_LOSS_POINTS * RISK_REWARD_RATIO
MAX_TRADES_PER_DAY = 3
DAILY_TARGET_POINTS = TAKE_PROFIT_POINTS * 2   # stop trading an instrument once its day hits this
NO_TRADE_MINUTES_AFTER_OPEN = 15              # let the opening range settle

# --- capital / compounding / one-time withdrawal (see capital_manager.py) ---
CAPITAL = 100_000
RISK_PCT_PER_TRADE = 0.01
MAX_RISK_PCT = 0.03
WITHDRAWAL_MULTIPLE = 2.0
ASSUMED_MARGIN_LEVERAGE = 10.0   # ~10x SPAN+exposure for NIFTY/BANKNIFTY futures

# --- session (NSE, IST) ---
MARKET_TZ = "Asia/Kolkata"
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"

# --- how much market history the weekly run pulls ---
INTRADAY_LOOKBACK_DAYS = 7     # Yahoo hard-caps 1-minute data at 7 calendar days
POLL_INTERVAL_SECONDS = 30     # live mode only
