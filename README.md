# Edge 1st — weekly paper bot

Runs **the first Edge strategy** (the archived `strategy_edge.py`, internally
"Strategy 5") over the **last week of real 1-minute NIFTY / BANKNIFTY data**,
applying **full Upstox index-futures charges**.

This is the config whose 2022–2026 walk-forward on the **1-minute exit model**
returned **+₹5,67,297 (NIFTY) / +₹7,51,154 (BANKNIFTY)** net of all costs.
This bot keeps that model exactly:

| Aspect | Setting |
|---|---|
| Signal | `strategy.py` (unchanged Strategy 5): ADX>20 regime filter → EMA20 trend → pullback into Camarilla PIVOT/S1–R1 zone → RSI reset → EMA9 reclaim |
| Entry evaluation | 5-minute bar boundaries |
| **Exit management** | **every 1-minute bar** — SL/TP fill at the exact level if that 1-min bar's range touches it; otherwise `should_exit_early()` exits at the 1-min close before price reaches the stop |
| Stop / target | ATR(14) or 10-bar swing (whichever is tighter) · target = 3× risk |
| Sizing | 1% risk of current equity, ~10× F&O leverage, NIFTY lot 65 / BANKNIFTY lot 30 (NSE-revised; verified live against Upstox's margin calculator 2026-09-04) |
| Capital | ₹2,00,000 per instrument, compounding, one-time 2× withdrawal (raised from ₹1,00,000 — that couldn't clear real margin for even 1 lot at the corrected sizes) |
| Costs | `upstox_fno_costs.py` — brokerage + STT + exchange txn + SEBI + stamp duty + GST, every trade (real Upstox rate card, not Zerodha's — verified 2026-09-05) |
| Orders | **none — paper only** |

## Market data — Upstox (default), Yahoo (fallback)

`config.DATA_SOURCE = "upstox"`. Override per-run with `EDGE1ST_DATA=yahoo`.

### Upstox
- Uses the **v3 historical-candle** API (`/minutes/1/...`) for past sessions
  plus the **v3 intraday** endpoint for the current day.
- Historical index candles are served **without a login**, so the bot runs
  out of the box on Upstox data.
- Run `python login.py` **once per trading day** (Upstox tokens expire ~03:30
  IST) to get an access token — this guarantees the freshest current-day
  1-minute bars and is the officially-supported path. It:
  1. reads `UPSTOX_API_KEY` / `UPSTOX_API_SECRET` from `config.py` (or env),
  2. opens the Upstox login page, catches the redirect on
     `http://127.0.0.1:8888/callback`,
  3. writes the token to `upstox_token.json` + `.env`.
- Your Upstox developer app's redirect URI **must be exactly**
  `http://127.0.0.1:8888/callback`.
- Instrument keys: `NSE_INDEX|Nifty 50`, `NSE_INDEX|Nifty Bank`.

### Yahoo (fallback)
- No login, ~15-min delayed, 7-calendar-day 1-minute cap. Used automatically
  if Upstox returns nothing, or force it with `EDGE1ST_DATA=yahoo`.

## Run

```bash
pip install -r requirements.txt

python login.py                    # once/day for Upstox (optional but recommended)
python edge_1st_bot.py             # replay last ~week, both instruments
python edge_1st_bot.py NIFTY       # one instrument
python edge_1st_bot.py 5           # last 5 days
python edge_1st_bot.py live        # forward paper loop, same 1-min exit logic

EDGE1ST_DATA=yahoo python edge_1st_bot.py    # force the Yahoo source
```

Writes `edge_1st_trades_<SYM>.csv` (full per-trade cost breakdown) and
`edge_1st_dashboard.html` (opens automatically).

## Notes / limits

- Yahoo hard-caps 1-minute history at 7 calendar days. Upstox's v3
  historical-candle API caps each *request* at ~31 days but the underlying
  archive goes back years -- `full_history_backtest.py` chains consecutive
  ~29-day requests to assemble multi-month/year continuous history; the live
  4-week dashboard (`edge_1st_bot.py`) just uses one request since it fits
  under the cap. Previous-session Camarilla pivots come from a separate
  daily pull (no such cap), so the first day in any window still trades.
- NSE indices report **zero volume**, so the strategy's volume-confirmation
  filter auto-disables (same as in the original backtests). Vendor OHLC prints
  differ slightly, so Upstox vs Yahoo runs won't match trade-for-trade.
- Self-contained: `strategy.py`, `indicators.py`, `capital_manager.py`,
  `upstox_fno_costs.py` are copies. Nothing here imports from the parent
  project or writes outside this folder.
- **Not financial advice, not a proven edge.** The +₹5–7 L headline depends
  entirely on the 1-minute exit assumption; on a 5-minute exit model the same
  strategy is a mild net loss (see the parent project's walk-forward notes).

## AIC Cloud deployment

This folder includes a FastAPI web service and scheduled paper-runner.

- Start command: `uvicorn dashboard_server:app --host 0.0.0.0 --port $PORT --workers 1`
- Required secret: `UPSTOX_ANALYTICS_TOKEN`
- Optional market-hours interval: `EDGE1ST_REFRESH_SECONDS=300`
- Optional off-hours interval: `EDGE1ST_OFF_HOURS_REFRESH_SECONDS=21600`
- Health check: `/healthz`
- Scheduler status: `/api/status`

Use exactly one web worker. Every worker starts a scheduler, so additional
workers would duplicate Upstox requests and report generation. Add secrets in
the AIC Cloud environment settings and never deploy `.env`.
