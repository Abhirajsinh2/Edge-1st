"""
Live tick-level market data for the Edge 1st bot via Upstox's WebSocket
Market Data Feed V3 -- used by run_live() instead of REST polling so a new
1-minute bar is picked up the moment Upstox pushes it, rather than up to
POLL_INTERVAL_SECONDS later (REST-side, Upstox's own community reports an
~8-20s backend lag on the intraday-candle endpoint no matter how often you
poll it -- the WebSocket feed is what they recommend for real real-time use).

Needs a real, authenticated Upstox access token (UPSTOX_ACCESS_TOKEN) --
unlike data_upstox.py's REST calls, this endpoint does not work anonymously.
Get one with `python login.py` (requires a LIVE Upstox app's
UPSTOX_API_KEY/UPSTOX_API_SECRET in .env, not a sandbox app's -- sandbox
apps cannot stream market data at all).

Uses the official `upstox-python-sdk` package (MarketDataStreamerV3), which
handles the connection, auth header, and Protobuf decoding for us. In
"full" mode, Upstox's own feed includes an `interval: "I1"` OHLC entry per
message for index instruments (NIFTY 50 / Nifty Bank) -- the current
still-forming 1-minute candle plus the previous one, live-updated on every
tick. See feeds[key].fullFeed.indexFF.marketOHLC.ohlc[] in the decoded
message.

NOTE: the `ts` field on each OHLC entry is assumed to be epoch
milliseconds (consistent with Upstox's other timestamp fields) -- this is
inferred from the protobuf schema, not confirmed against a live message,
since this was built outside market hours. Verify against a real message
the first time this runs live (print one raw `_on_message` payload) and
adjust the `unit="ms"` below if it turns out to be seconds instead.
"""

import threading

import pandas as pd
import upstox_client
from zoneinfo import ZoneInfo

import config
import data_upstox

TZ = ZoneInfo(config.MARKET_TZ)

_lock = threading.Lock()
_bars: dict = {}          # symbol -> {ts_epoch_ms: {open,high,low,close,volume}}
_connected = threading.Event()
_streamer = None


def _key_to_symbol() -> dict:
    return {v: k for k, v in config.UPSTOX_INSTRUMENT_KEYS.items()}


def _on_message(message: dict) -> None:
    key_to_sym = _key_to_symbol()
    for inst_key, feed in (message or {}).get("feeds", {}).items():
        symbol = key_to_sym.get(inst_key)
        if symbol is None:
            continue
        full = feed.get("fullFeed", {}) or {}
        segment_feed = full.get("indexFF") or full.get("marketFF") or {}
        ohlc_list = ((segment_feed.get("marketOHLC") or {}).get("ohlc")) or []
        for c in ohlc_list:
            if c.get("interval") != "I1":
                continue
            try:
                ts_ms = int(c["ts"])
                bar = {
                    "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": float(c.get("vol", 0) or 0),
                }
            except (KeyError, ValueError, TypeError):
                continue
            with _lock:
                _bars.setdefault(symbol, {})[ts_ms] = bar


def _on_open():
    _connected.set()
    print("upstox_stream: live feed connected")


def _on_error(err):
    print(f"upstox_stream: error: {err}")


def _on_close():
    _connected.clear()
    print("upstox_stream: live feed disconnected")


def start(symbols=None) -> None:
    """Open the WebSocket and start receiving live 1-min candles. Returns
    immediately -- the SDK runs the socket on its own background thread and
    delivers bars into an in-memory buffer read by get_live_df()."""
    global _streamer
    symbols = symbols or config.INSTRUMENTS
    tok = data_upstox.token()
    if not tok:
        raise RuntimeError(
            "No Upstox access token -- run `python login.py` first (with a "
            "LIVE app's UPSTOX_API_KEY/UPSTOX_API_SECRET in .env). The "
            "live WebSocket feed needs real auth, unlike REST candles."
        )

    cfg = upstox_client.Configuration()
    cfg.access_token = tok
    keys = [config.UPSTOX_INSTRUMENT_KEYS[s] for s in symbols
            if s in config.UPSTOX_INSTRUMENT_KEYS]

    _streamer = upstox_client.MarketDataStreamerV3(
        upstox_client.ApiClient(cfg), keys, "full")
    _streamer.on("open", _on_open)
    _streamer.on("message", _on_message)
    _streamer.on("error", _on_error)
    _streamer.on("close", _on_close)
    _streamer.connect()


def wait_connected(timeout: float = 10.0) -> bool:
    return _connected.wait(timeout=timeout)


def is_connected() -> bool:
    return _connected.is_set()


def get_live_df(symbol: str, seed: pd.DataFrame = None) -> pd.DataFrame:
    """1-min OHLCV DataFrame for `symbol`, IST-indexed -- REST-seeded
    history (for indicator lookback) combined with whatever the live stream
    has received so far. Safe to call on a tight loop: it's a local memory
    read, no network call. Falls back to `seed` alone if the stream hasn't
    delivered anything yet (e.g. feed not connected)."""
    with _lock:
        bars = dict(_bars.get(symbol, {}))
    if not bars:
        return seed if seed is not None else pd.DataFrame()

    rows = []
    for ts_ms, o in sorted(bars.items()):
        rows.append({"timestamp": pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(TZ), **o})
    live_df = pd.DataFrame(rows).set_index("timestamp")

    if seed is None or seed.empty:
        return live_df
    combined = pd.concat([seed, live_df])
    return combined[~combined.index.duplicated(keep="last")].sort_index()
