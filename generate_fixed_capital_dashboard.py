"""
Builds fixed-capital.html from fixed_capital_results.json -- the "no reinvest"
sibling of generate_full_history_dashboard.py.

Same static, JS-free dark style. The difference is the money model being shown:
every trade is sized off a FLAT Rs 2,00,000 stake for the whole history, profit
is never put back to work, and there is no 2x-stake withdrawal. The equity curve
is therefore just: Rs 2,00,000 + cumulative net P&L, plotted against calendar
time (first trade -> last trade).

edge_1st_bot.py, generate_full_history_dashboard.py and the live 4-week refresh
are all left untouched.
"""

import json
from datetime import datetime as dt

with open("fixed_capital_results.json", encoding="utf-8") as f:
    results = json.load(f)

COLORS = {"NIFTY": "#58a6ff", "BANKNIFTY": "#d29922"}


def fmt(v):
    return f"{v:+,.0f}"


def _pdate(s):
    return dt.fromisoformat(str(s))


def _naive(d):
    return d.replace(tzinfo=None)


def date_range(trades):
    if not trades:
        return None, None
    return _pdate(trades[0]["date"]), _pdate(trades[-1]["date"])


def lot_note(sym, trades):
    if not trades:
        return ""
    qtys = sorted({t["quantity"] for t in trades})
    if len(qtys) == 1:
        detail = (f"every trade was {qtys[0]} qty (1 lot) -- position size never "
                  f"changed, because 1%-risk sizing always saw the same flat "
                  f"Rs 2,00,000 stake, win or lose.")
    else:
        detail = (f"position size ranged {qtys[0]}&ndash;{qtys[-1]} qty purely "
                  f"from trade-to-trade stop distance (a wider stop buys fewer "
                  f"units for the same 1% of the flat stake) -- NOT from the "
                  f"balance growing.")
    return f"<div class=sub>{detail}</div>"


def card_html(label, summary, account, trades):
    col = "#43D9AD" if summary["net"] >= 0 else "#f7768e"
    d0, d1 = date_range(trades)
    span = (f"{d0:%b %Y} &rarr; {d1:%b %Y}" if d0 else "&mdash;")
    return (
        f"<div class=card><h2>{label}</h2>"
        f"<div class=big style='color:{col}'>{account['return_pct']:+.1f}%</div>"
        f"<div class=sub>{span} &middot; {summary['trades']} trades &middot; {summary['win_rate']:.0f}% win</div>"
        f"<div class=sub>Rs {account['initial']:,.0f} stake (flat) &rarr; "
        f"total value Rs {account['total_value']:,.0f}</div>"
        f"<div class=sub>All profit banked as realised &mdash; never reinvested, no withdrawal step</div>"
        f"<div class=sub>gross Rs {fmt(summary['gross'])} &middot; "
        f"charges Rs {summary['charges']:,.0f} &middot; NET Rs {fmt(summary['net'])}</div></div>"
    )


def equity_curve_svg(results):
    """Rs 2,00,000 + cumulative net P&L vs calendar time. No withdrawal to mark."""
    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 900, 320, 78, 20, 16, 46

    series = {}
    for sym, r in results.items():
        trades = r["trades"]
        if not trades:
            continue
        pts = [(_naive(_pdate(trades[0]["entry_time"])), trades[0]["equity_before"])]
        for t in trades:
            pts.append((_naive(_pdate(t["exit_time"])), t["equity_after"]))
        series[sym] = pts

    if not series:
        return "<p class=sub>No trades to plot.</p>"

    all_t = [t for pts in series.values() for t, _ in pts]
    all_v = [v for pts in series.values() for _, v in pts]
    t_min, t_max = min(all_t), max(all_t)
    span_t = (t_max - t_min).total_seconds() or 1.0
    min_v, max_v = min(all_v), max(all_v)
    pad_v = (max_v - min_v) * 0.06 or 1.0
    min_v -= pad_v
    max_v += pad_v
    span_v = (max_v - min_v) or 1.0

    def x(t):
        return PAD_L + ((t - t_min).total_seconds() / span_t) * (W - PAD_L - PAD_R)

    def y(v):
        return PAD_T + (1 - (v - min_v) / span_v) * (H - PAD_T - PAD_B)

    grid = ""
    for g in range(5):
        gy = PAD_T + (g / 4) * (H - PAD_T - PAD_B)
        v = max_v - (g / 4) * span_v
        grid += f"<line x1='{PAD_L}' y1='{gy:.1f}' x2='{W-PAD_R}' y2='{gy:.1f}' stroke='#21262d'/>"
        grid += f"<text x='6' y='{gy+4:.1f}' font-size='11' fill='#8b949e'>Rs {v:,.0f}</text>"

    for g in range(6):
        frac = g / 5
        gx = PAD_L + frac * (W - PAD_L - PAD_R)
        tt = t_min + (t_max - t_min) * frac
        grid += f"<line x1='{gx:.1f}' y1='{PAD_T}' x2='{gx:.1f}' y2='{H-PAD_B}' stroke='#21262d'/>"
        anchor = "start" if g == 0 else ("end" if g == 5 else "middle")
        grid += (f"<text x='{gx:.1f}' y='{H-PAD_B+18:.1f}' font-size='11' fill='#8b949e' "
                 f"text-anchor='{anchor}'>{tt:%b %Y}</text>")

    # flat-stake reference line
    base = None
    for r in results.values():
        if r["account"].get("initial"):
            base = r["account"]["initial"]
            break
    if base is not None and min_v <= base <= max_v:
        by = y(base)
        grid += (f"<line x1='{PAD_L}' y1='{by:.1f}' x2='{W-PAD_R}' y2='{by:.1f}' "
                 f"stroke='#6e7681' stroke-dasharray='4 4'/>")
        grid += (f"<text x='{W-PAD_R:.1f}' y='{by-5:.1f}' font-size='10.5' fill='#6e7681' "
                 f"text-anchor='end'>Rs {base:,.0f} stake</text>")

    paths, legend = "", ""
    for sym, pts in series.items():
        color = COLORS.get(sym, "#c9d1d9")
        d = "M " + " L ".join(f"{x(t):.1f} {y(v):.1f}" for t, v in pts)
        paths += f"<path d='{d}' fill='none' stroke='{color}' stroke-width='2'/>"
        d0, d1 = pts[0][0], pts[-1][0]
        legend += (f"<span style='color:{color}'>&#9632;</span> {sym} "
                   f"({d0:%d %b %Y} &rarr; {d1:%d %b %Y})&nbsp;&nbsp;&nbsp;")

    svg = (f"<svg viewBox='0 0 {W} {H}' style='width:100%;height:auto'>"
           f"{grid}{paths}</svg>")
    note = ("Line = Rs 2,00,000 flat stake + cumulative net P&L. Because sizing "
            "never sees this line, only the strategy's own wins and losses move "
            "it -- there is no compounding feedback.")
    return (f"<div class='sub' style='margin-bottom:6px'>{legend}</div>"
            f"{svg}"
            f"<div class='sub' style='margin-top:6px'>{note}</div>")


def trade_rows_html(sym, trades):
    rows = ""
    running = 0.0
    for t in trades:
        running += t["net_pnl_inr"]
        col = "#43D9AD" if t["net_pnl_inr"] >= 0 else "#f7768e"
        rcol = "#43D9AD" if running >= 0 else "#f7768e"
        rows += (
            f"<tr><td>{t['date']}</td><td>{t['direction']}</td>"
            f"<td>{t['entry_price']}</td><td>{t['exit_price']}</td>"
            f"<td>{t['exit_reason']}</td><td>{t['quantity']}</td>"
            f"<td style='color:{col}'>{t['net_pnl_inr']:+,.0f}</td>"
            f"<td style='color:{rcol}'>{running:+,.0f}</td></tr>"
        )
    return rows


total_cards = "".join(
    card_html(sym, r["summary"], r["account"], r["trades"]) for sym, r in results.items()
)

combined_initial = sum(r["account"]["initial"] for r in results.values())
combined_total = sum(r["account"]["total_value"] for r in results.values())
combined_net = sum(r["summary"]["net"] for r in results.values())
combined_trades = sum(r["summary"]["trades"] for r in results.values())
combined_return = (combined_total - combined_initial) / combined_initial * 100 if combined_initial else 0

_alltrades = [t for r in results.values() for t in r["trades"]]
_d0 = min((_pdate(t["date"]) for t in _alltrades), default=None)
_d1 = max((_pdate(t["date"]) for t in _alltrades), default=None)
_span = f"{_d0:%d %b %Y} &rarr; {_d1:%d %b %Y}" if _d0 else "&mdash;"

combined_card = (
    f"<div class=card style='border-color:#58a6ff'><h2>Combined</h2>"
    f"<div class=big style='color:{'#43D9AD' if combined_net >= 0 else '#f7768e'}'>{combined_return:+.1f}%</div>"
    f"<div class=sub>{_span} &middot; {combined_trades} trades</div>"
    f"<div class=sub>Rs {combined_initial:,.0f} stake (flat) &rarr; total value Rs {combined_total:,.0f}</div>"
    f"<div class=sub>NET Rs {fmt(combined_net)}</div></div>"
)

chart_html = equity_curve_svg(results)

sections = ""
for sym, r in results.items():
    sections += (
        f"<h2 style='color:#8b949e;font-size:1rem;margin-top:32px'>{sym} &mdash; all trades</h2>"
        f"{lot_note(sym, r['trades'])}"
        f"<table><tr><th>Date</th><th>Dir</th><th>Entry</th><th>Exit</th><th>Reason</th>"
        f"<th>Qty</th><th>Net Rs</th><th>Cumulative P&amp;L</th></tr>"
        f"{trade_rows_html(sym, r['trades'])}</table>"
    )

html = f"""<!doctype html><html><head><meta charset=UTF-8>
<title>Edge 1st — fixed stake (no reinvest)</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}}
h1{{margin:0 0 4px}} .meta{{color:#8b949e;margin-bottom:20px;font-size:.9rem}}
a{{color:#58a6ff}}
.cards{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;min-width:240px}}
.card h2{{margin:0 0 8px;font-size:1rem;color:#8b949e}}
.big{{font-size:1.8rem;font-weight:700}} .sub{{color:#8b949e;font-size:.8rem;margin-top:6px}}
.chart-card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse;font-size:.8rem;margin-bottom:8px}}
th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid #21262d}} th{{color:#8b949e}}
</style></head><body>
<p><a href="index.html">&larr; last-4-weeks dashboard</a> &middot; <a href="today.html">today</a> &middot; <a href="full-history.html">full-history (compounding) &rarr;</a></p>
<h1>Edge 1st &mdash; full history, fixed stake (no reinvest)</h1>
<div class=meta>Every trade sized off a FLAT Rs 2,00,000 stake for the whole history (1% risk + margin cap) &middot;
realised P&amp;L is banked, never put back to work &middot; no 2&times;-stake withdrawal &middot;
same strategy / 1-minute exit model / full Upstox F&amp;O costs as the compounding run &middot; window {_span} &middot;
generated {dt.now():%Y-%m-%d %H:%M} &middot; paper only, no real orders</div>
<div class=cards>{total_cards}{combined_card}</div>
<div class=chart-card>
<h2 style='color:#8b949e;font-size:1rem;margin-top:0'>Account value over time (fixed stake, no reinvest)</h2>
{chart_html}
</div>
{sections}
</body></html>"""

with open("fixed-capital.html", "w", encoding="utf-8") as f:
    f.write(html)

print("wrote fixed-capital.html")
