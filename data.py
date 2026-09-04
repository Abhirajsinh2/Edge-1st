"""
Last-week market data for the Edge 1st bot — free, no-login Yahoo Finance
1-minute NIFTY / BANKNIFTY candles.

Yahoo hard-caps intraday 1-minute history at 7 calendar days, which is
exactly the window this bot works on. Daily bars (for the previous-session
Camarilla pivots) come from a separate 1-month/1-day pull so even the first
trading day in the intraday window gets valid pivots.

Yahoo throttles aggressively and returns an EMPTY frame when it does, so
every pull is cached briefly, retried once on empty, and falls back to the
last good frame.
"""

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

import config

TZ = ZoneInfo(config.MARKET_TZ)

_CACHE_TTL = 90.0
_STALE_MAX_AGE = 1800.0
_RETRY_BACKOFF = 2.0
_cache: dict = {}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert(TZ)
    else:
        df.index = df.index.tz_convert(TZ)
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                            "Close": "close", "Volume": "volume"})
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[cols].dropna()


def _download(ticker: str, period: str, interval: str) -> pd.DataFrame:
    key = (ticker, period, interval)
    now = time.time()
    cached = _cache.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1].copy()

    def _raw() -> pd.DataFrame:
        try:
            return _normalise(yf.download(ticker, period=period, interval=interval,
                                          progress=False, auto_adjust=True, threads=False))
        except Exception as exc:
            print(f"  data: download error {ticker} {period}/{interval}: {exc}")
            return pd.DataFrame()

    df = _raw()
    if df.empty:
        time.sleep(_RETRY_BACKOFF)
        df = _raw()

    if not df.empty:
        _cache[key] = (now, df.copy())
        return df
    if cached and now - cached[0] < _STALE_MAX_AGE:
        print(f"  data: serving stale {ticker} {period}/{interval}")
        return cached[1].copy()
    return pd.DataFrame()


def get_week_1min(symbol: str, days: int = None) -> pd.DataFrame:
    """1-minute OHLCV for the last `days` calendar days (default config value)."""
    days = days or config.INTRADAY_LOOKBACK_DAYS
    ticker = config.YAHOO_SYMBOLS[symbol]
    df = _download(ticker, f"{days}d", "1m")
    if df.empty:
        df = _download(ticker, f"{days}d", "5m")   # last-ditch coarser grain
    return df


def get_daily(symbol: str, period: str = "1mo") -> pd.DataFrame:
    """Daily OHLC — used only for previous-session Camarilla pivots."""
    return _download(config.YAHOO_SYMBOLS[symbol], period, "1d")


def prev_day_ohlc_map(symbol: str) -> dict:
    """{date -> {high,low,close} of the previous trading day} for pivot lookups."""
    daily = get_daily(symbol)
    out = {}
    if daily.empty or len(daily) < 2:
        return out
    dates = list(daily.index.date)
    for i in range(1, len(dates)):
        pr = daily.iloc[i - 1]
        out[dates[i]] = {"high": float(pr["high"]), "low": float(pr["low"]),
                         "close": float(pr["close"])}
    return out
