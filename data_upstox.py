"""
Upstox market data for the Edge 1st bot — drop-in replacement for data.py,
same public interface (get_week_1min / get_daily / prev_day_ohlc_map) so
edge_1st_bot.py doesn't care which source it gets.

Uses the Upstox v3 historical-candle REST API for past sessions plus the
v3 intraday endpoint for the current day, so the "last week" window is
complete right up to the latest 1-minute bar.

Auth
----
Needs a daily Upstox access token (they expire ~03:30 IST). Get one with:

    python login.py            # opens browser, paste redirect URL back

The token is read from, in order:
  1. env var  UPSTOX_ACCESS_TOKEN
  2. edge_1st/.env             (UPSTOX_ACCESS_TOKEN=...)
  3. edge_1st/upstox_token.json  ({"access_token": "..."} — written by login.py)
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import config

TZ = ZoneInfo(config.MARKET_TZ)
V3 = "https://api.upstox.com/v3/historical-candle"
_HERE = Path(__file__).parent

_CACHE_TTL = 90.0
_cache: dict = {}


# ── auth ─────────────────────────────────────────────────────────────────

def _token() -> str:
    """Access token if we have one, else "". Upstox's historical-candle and
    intraday endpoints serve index data without a valid token; a real token
    is only strictly needed for live quotes / order APIs. login.py writes one
    so the freshest current-day bars are guaranteed."""
    # Prefer the one-year read-only Analytics Token for market-data use.
    # A normal daily OAuth token remains supported as a fallback.
    tok = os.getenv("UPSTOX_ANALYTICS_TOKEN") or os.getenv("UPSTOX_ACCESS_TOKEN")
    if tok:
        return tok.strip()

    env = _HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(("UPSTOX_ANALYTICS_TOKEN=", "UPSTOX_ACCESS_TOKEN=")):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value

    tj = _HERE / "upstox_token.json"
    if tj.exists():
        try:
            return json.loads(tj.read_text())["access_token"]
        except Exception:
            pass
    return ""


def has_token() -> bool:
    return bool(_token())


def _headers() -> dict:
    return {"Accept": "application/json", "Authorization": f"Bearer {_token() or 'anonymous'}"}


# ── raw candle pulls ─────────────────────────────────────────────────────

def _candles_to_df(candles: list) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low",
                                        "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(TZ)
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    return df.sort_index().astype(float)


def _get(url: str) -> list:
    key = url
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    for attempt in (1, 2):
        try:
            r = requests.get(url, headers=_headers(), timeout=30)
            if r.status_code == 401:
                raise RuntimeError("Upstox token rejected (401) — it likely expired; "
                                   "re-run  python login.py")
            r.raise_for_status()
            candles = r.json().get("data", {}).get("candles", [])
            _cache[key] = (now, candles)
            return candles
        except RuntimeError:
            raise
        except Exception as exc:
            if attempt == 2:
                print(f"  upstox: {exc}  ({url.split('/v3/')[-1]})")
                return []
            time.sleep(2.0)
    return []


def _hist_1min(inst_key: str, from_d: datetime, to_d: datetime) -> pd.DataFrame:
    k = quote(inst_key, safe="")
    url = f"{V3}/{k}/minutes/1/{to_d:%Y-%m-%d}/{from_d:%Y-%m-%d}"
    return _candles_to_df(_get(url))


def _intraday_1min(inst_key: str) -> pd.DataFrame:
    k = quote(inst_key, safe="")
    return _candles_to_df(_get(f"{V3}/intraday/{k}/minutes/1"))


def _hist_daily(inst_key: str, from_d: datetime, to_d: datetime) -> pd.DataFrame:
    k = quote(inst_key, safe="")
    url = f"{V3}/{k}/days/1/{to_d:%Y-%m-%d}/{from_d:%Y-%m-%d}"
    return _candles_to_df(_get(url))


# ── public interface (matches data.py) ───────────────────────────────────

def get_week_1min(symbol: str, days: int = None) -> pd.DataFrame:
    days = days or config.INTRADAY_LOOKBACK_DAYS
    inst = config.UPSTOX_INSTRUMENT_KEYS[symbol]
    now = datetime.now(TZ)
    hist = _hist_1min(inst, now - timedelta(days=days + 1), now)
    intr = _intraday_1min(inst)
    frames = [f for f in (hist, intr) if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    cutoff = now - timedelta(days=days)
    return df[df.index >= cutoff]


def get_daily(symbol: str, lookback_days: int = 40) -> pd.DataFrame:
    inst = config.UPSTOX_INSTRUMENT_KEYS[symbol]
    now = datetime.now(TZ)
    return _hist_daily(inst, now - timedelta(days=lookback_days), now)


def prev_day_ohlc_map(symbol: str) -> dict:
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
