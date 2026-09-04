"""
Technical indicators used by the strategy:
  - EMA (exponential moving average)
  - RSI (momentum cross-reference filter)
  - VWAP (intraday cross-reference filter)
  - Camarilla pivot points (support/resistance context)
  - A rule-based supply/demand zone detector
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """df must have columns: high, low, close. Wilder-style EMA-smoothed ATR."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """df must have columns: high, low, close."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr14 = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period).mean() / atr14
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, min_periods=period).mean() / atr14

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, min_periods=period).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """df must have columns: high, low, close, volume, and cover a single session."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return (typical * df["volume"]).cumsum() / cum_vol


def camarilla_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Standard Camarilla pivot formula using the previous session's H/L/C."""
    rng = prev_high - prev_low
    c = prev_close
    return {
        "R4": c + rng * 1.1 / 2,
        "R3": c + rng * 1.1 / 4,
        "R2": c + rng * 1.1 / 6,
        "R1": c + rng * 1.1 / 12,
        "PIVOT": c,
        "S1": c - rng * 1.1 / 12,
        "S2": c - rng * 1.1 / 6,
        "S3": c - rng * 1.1 / 4,
        "S4": c - rng * 1.1 / 2,
    }


def find_supply_demand_zones(df: pd.DataFrame, lookback: int = 20,
                              consolidation_range_pct: float = 0.15) -> list:
    """
    Simplified, rule-based supply/demand zone detector (there is no single
    industry-standard formula for this, so treat it as a tunable heuristic):

      1. Look for a small (2-candle) "base" where price is tightly
         consolidated (range below consolidation_range_pct of price).
      2. Check if the candle right after the base is a strong, wide-bodied
         breakout candle.
      3. If it broke upward -> the base becomes a "demand" zone.
         If it broke downward -> the base becomes a "supply" zone.

    Returns a list of dicts: {"type": "demand"/"supply", "low": .., "high": ..}
    Only the most recent zone of each type is kept.
    """
    zones = []
    if len(df) < lookback + 2:
        return zones

    window = df.tail(lookback + 1).reset_index(drop=True)
    for i in range(2, len(window) - 1):
        base = window.iloc[max(0, i - 2):i]
        breakout = window.iloc[i]

        base_high = base["high"].max()
        base_low = base["low"].min()
        base_mid = (base_high + base_low) / 2
        base_range_pct = (base_high - base_low) / base_mid * 100 if base_mid else 999

        if base_range_pct > consolidation_range_pct:
            continue  # base wasn't tight enough

        body = abs(breakout["close"] - breakout["open"])
        candle_range = breakout["high"] - breakout["low"]
        if candle_range == 0 or body / candle_range < 0.6:
            continue  # breakout candle wasn't strong/directional enough

        if breakout["close"] > base_high:
            zones.append({"type": "demand", "low": base_low, "high": base_high})
        elif breakout["close"] < base_low:
            zones.append({"type": "supply", "low": base_low, "high": base_high})

    latest = {}
    for z in zones:
        latest[z["type"]] = z  # keep only the latest of each type
    return list(latest.values())


def near_zone(price: float, zones: list, zone_type: str, buffer_points: float) -> bool:
    for z in zones:
        if z["type"] != zone_type:
            continue
        if (z["low"] - buffer_points) <= price <= (z["high"] + buffer_points):
            return True
    return False
