"""
"EDGE 1ST" — the first Edge strategy. Verbatim copy of the archived
strategy_edge.py ("Strategy 5"); logic unchanged, kept here so the edge_1st
bot stays self-contained and never drifts from what was backtested.

Strategy 5 - "Edge": trend-pullback with a regime filter, ATR-based dynamic
risk, and volume confirmation (applied only where real volume data exists -
NIFTY/BANKNIFTY report zero volume from Yahoo since they're indices, so the
check is skipped there rather than blocking every trade).

Built from scratch after backtesting strategy.py / strategy2.py / strategy3.py
at the project's 3:1 reward:risk showed NONE of them clear the ~25% win rate
needed to break even (see the earlier backtest run) — this isn't a tweak of
those, it's a different set of design choices reasoned from why they failed:

  1. REGIME FILTER — only trade when ADX(14) on the reference timeframe
     confirms an actual trend (> 20). strategy2/strategy3 have no such
     filter and lost badly: a mean-reversion/momentum signal firing in
     flat, choppy conditions has no edge to exploit.
  2. TREND DIRECTION — reference-timeframe EMA20 slope, confirmed against
     the session VWAP wherever real volume data exists.
  3. ENTRY — don't chase the trend (strategy3's approach, which had the
     worst win rates of all four). Wait for price to pull back into a
     Camarilla PIVOT/S1 (longs) or PIVOT/R1 (shorts) zone, RSI recycling
     from an extreme back in the trend direction, and price reclaiming the
     entry-timeframe EMA9 in the trend direction. This generalizes the
     TITAN.NS bot's long-only pullback logic (75% win rate there) to both
     directions.
  4. RISK — an ATR-based dynamic stop instead of a fixed point value: a
     flat 50-point NIFTY stop or $3 SPY stop ignores that day's actual
     volatility. Target is config.RISK_REWARD_RATIO x the stop distance.
  5. VOLUME CONFIRMATION — entry-bar volume above its recent average,
     enforced only when the instrument actually reports non-zero volume.

Interface: generate_signal(df_entry, df_ref, prev_day_ohlc, instrument="DEFAULT")
           -> (Signal, debug)  - same as the other strategies, works as a
           drop-in for multi_bot.py / multi_bot_india.py / backtest.py /
           backtest_us.py.
Also exposes compute_stop_and_target(df_entry, entry_price, direction) ->
(stop, target); the engines use this instead of the fixed
config.STOP_LOSS_POINTS / TAKE_PROFIT_POINTS whenever a strategy module
defines it.

No hardcoded market-open time or currency assumptions, so unlike strategy4 /
strategy4_india this ONE file works unmodified for NIFTY, BANKNIFTY, SPY,
and QQQ alike.
"""

import pandas as pd

import config
from indicators import ema, rsi, vwap, atr, adx, camarilla_pivots


class Signal:
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


EMA_FAST = 9
EMA_TREND = 20
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
ADX_MIN_STRENGTH = 20
RSI_LONG_TRIGGER = 45     # "was oversold" threshold checked on the way back up
RSI_SHORT_TRIGGER = 55    # "was overbought" threshold checked on the way back down
RSI_LOOKBACK_BARS = 3     # bars (excl. current) checked for the RSI extreme
PIVOT_ZONE_BUFFER_PCT = 0.0025
SWING_LOOKBACK = 10
VOLUME_LOOKBACK = 10
VOLUME_MULTIPLIER = 1.2


def resample(df_1m: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    o = df_1m["open"].resample(rule).first()
    h = df_1m["high"].resample(rule).max()
    l = df_1m["low"].resample(rule).min()
    c = df_1m["close"].resample(rule).last()
    v = df_1m["volume"].resample(rule).sum()
    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    return out.dropna()


def _has_real_volume(df: pd.DataFrame) -> bool:
    return "volume" in df.columns and df["volume"].sum() > 0


def generate_signal(df_entry: pd.DataFrame, df_ref: pd.DataFrame, prev_day_ohlc: dict,
                     instrument: str = "DEFAULT") -> tuple:
    debug = {}
    min_ref = max(EMA_TREND, ADX_PERIOD) + 3
    min_entry = max(RSI_PERIOD, ATR_PERIOD, VOLUME_LOOKBACK) + 3
    if len(df_ref) < min_ref or len(df_entry) < min_entry:
        return Signal.NONE, debug

    # --- regime filter: only trade a confirmed trend, not chop ---
    adx_val = adx(df_ref, ADX_PERIOD).iloc[-1]
    debug["adx"] = round(float(adx_val), 1) if pd.notna(adx_val) else None
    if pd.isna(adx_val) or adx_val < ADX_MIN_STRENGTH:
        return Signal.NONE, debug

    # --- trend direction on the reference timeframe ---
    ema_trend = ema(df_ref["close"], EMA_TREND)
    trend_rising = ema_trend.iloc[-1] > ema_trend.iloc[-2]
    trend_falling = ema_trend.iloc[-1] < ema_trend.iloc[-2]

    vwap_ok_long = vwap_ok_short = True
    if _has_real_volume(df_ref):
        session_day = df_ref.index[-1].date()
        df_ref_session = df_ref[df_ref.index.date == session_day]
        vw = vwap(df_ref_session).iloc[-1]
        debug["vwap"] = round(float(vw), 2) if pd.notna(vw) else None
        vwap_ok_long = pd.notna(vw) and df_ref["close"].iloc[-1] > vw
        vwap_ok_short = pd.notna(vw) and df_ref["close"].iloc[-1] < vw

    long_regime = bool(trend_rising and vwap_ok_long)
    short_regime = bool(trend_falling and vwap_ok_short)
    debug["long_regime"], debug["short_regime"] = long_regime, short_regime
    if not (long_regime or short_regime):
        return Signal.NONE, debug

    # --- entry-timeframe pullback setup ---
    ema_fast = ema(df_entry["close"], EMA_FAST)
    rsi14 = rsi(df_entry["close"], RSI_PERIOD)
    pivots = camarilla_pivots(prev_day_ohlc["high"], prev_day_ohlc["low"], prev_day_ohlc["close"])

    session_day_e = df_entry.index[-1].date()
    session_entry = df_entry[df_entry.index.date == session_day_e]

    volume_ok = True
    if _has_real_volume(df_entry) and len(df_entry) > VOLUME_LOOKBACK:
        avg_vol = df_entry["volume"].iloc[-(VOLUME_LOOKBACK + 1):-1].mean()
        volume_ok = bool(avg_vol > 0 and df_entry["volume"].iloc[-1] > VOLUME_MULTIPLIER * avg_vol)
    debug["volume_ok"] = volume_ok

    if long_regime:
        zone_lo = min(pivots["S1"], pivots["PIVOT"]) * (1 - PIVOT_ZONE_BUFFER_PCT)
        zone_hi = max(pivots["S1"], pivots["PIVOT"]) * (1 + PIVOT_ZONE_BUFFER_PCT)
        recent_low = session_entry["low"].iloc[-2:].min() if len(session_entry) >= 2 else session_entry["low"].iloc[-1]
        pulled_in = bool(zone_lo <= recent_low <= zone_hi)
        rsi_reset = bool((rsi14.iloc[-(RSI_LOOKBACK_BARS + 1):-1] < RSI_LONG_TRIGGER).any())
        rsi_turn = bool(rsi14.iloc[-1] > rsi14.iloc[-2])
        reclaim = bool(df_entry["close"].iloc[-1] > ema_fast.iloc[-1]
                       and df_entry["close"].iloc[-2] <= ema_fast.iloc[-2])
        debug.update(pulled_in=pulled_in, rsi_reset=rsi_reset, rsi_turn=rsi_turn, reclaim=reclaim)
        if pulled_in and rsi_reset and rsi_turn and reclaim and volume_ok:
            return Signal.LONG, debug

    if short_regime:
        zone_lo = min(pivots["R1"], pivots["PIVOT"]) * (1 - PIVOT_ZONE_BUFFER_PCT)
        zone_hi = max(pivots["R1"], pivots["PIVOT"]) * (1 + PIVOT_ZONE_BUFFER_PCT)
        recent_high = session_entry["high"].iloc[-2:].max() if len(session_entry) >= 2 else session_entry["high"].iloc[-1]
        pushed_in = bool(zone_lo <= recent_high <= zone_hi)
        rsi_reset = bool((rsi14.iloc[-(RSI_LOOKBACK_BARS + 1):-1] > RSI_SHORT_TRIGGER).any())
        rsi_turn = bool(rsi14.iloc[-1] < rsi14.iloc[-2])
        reclaim = bool(df_entry["close"].iloc[-1] < ema_fast.iloc[-1]
                       and df_entry["close"].iloc[-2] >= ema_fast.iloc[-2])
        debug.update(pushed_in=pushed_in, rsi_reset=rsi_reset, rsi_turn=rsi_turn, reclaim=reclaim)
        if pushed_in and rsi_reset and rsi_turn and reclaim and volume_ok:
            return Signal.SHORT, debug

    return Signal.NONE, debug


def should_exit_early(df_entry: pd.DataFrame, direction: str) -> bool:
    """
    Cuts a failing setup before it has to ride all the way to the full
    stop: if price closes back through EMA9 against the trade AND RSI has
    turned back the wrong way, the pullback-reclaim thesis is invalidated.
    """
    if len(df_entry) < EMA_FAST + 1:
        return False
    ema_fast = ema(df_entry["close"], EMA_FAST)
    rsi14 = rsi(df_entry["close"], RSI_PERIOD)
    close = df_entry["close"].iloc[-1]
    if direction == Signal.LONG:
        return bool(close < ema_fast.iloc[-1] and rsi14.iloc[-1] < RSI_LONG_TRIGGER)
    return bool(close > ema_fast.iloc[-1] and rsi14.iloc[-1] > RSI_SHORT_TRIGGER)


def compute_stop_and_target(df_entry: pd.DataFrame, entry_price: float, direction: str) -> tuple:
    atr14 = atr(df_entry, ATR_PERIOD).iloc[-1]
    if pd.isna(atr14) or atr14 <= 0:
        atr14 = entry_price * 0.002   # defensive fallback; warm-up guard means this shouldn't trigger

    if direction == Signal.LONG:
        swing = df_entry["low"].iloc[-SWING_LOOKBACK:].min()
        atr_stop = entry_price - atr14
        stop = max(swing, atr_stop)          # tighter of the two -> closer to entry -> smaller risk
        stop_distance = entry_price - stop
        target = entry_price + config.RISK_REWARD_RATIO * stop_distance
    else:
        swing = df_entry["high"].iloc[-SWING_LOOKBACK:].max()
        atr_stop = entry_price + atr14
        stop = min(swing, atr_stop)
        stop_distance = stop - entry_price
        target = entry_price - config.RISK_REWARD_RATIO * stop_distance

    return round(float(stop), 4), round(float(target), 4)
