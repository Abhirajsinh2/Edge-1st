"""
Zerodha-equivalent NSE index-FUTURES charges calculator (paper trading, for
projection purposes only — you're not actually paying these, this just
tells you what the trade would have cost on a real Zerodha account).

This project trades NIFTY/BANKNIFTY as a plain points-times-lot-size payoff
(no strike selection, no premium decay), which mechanically matches an
INDEX FUTURES position, not an option — so futures charges apply here, not
zerodha_costs.py's equity-intraday rates.

Rates used (per-order / per-turnover, standard Zerodha index-futures,
2026 rate card — these occasionally change, recheck against
zerodha.com/charges before relying on this for real capital):

  Brokerage        : 0.03% of order value, capped at ₹20/executed order
                      (charged on BOTH the buy leg and the sell leg)
  STT               : 0.02% on SELL turnover only
  Exchange (NSE)
  transaction chg   : 0.0019% on turnover, both sides
  SEBI turnover fee : ₹10 per crore (0.0001%), both sides
  Stamp duty        : 0.002% on BUY turnover only
  GST               : 18% on (brokerage + exchange txn charges + SEBI fee)
"""

BROKERAGE_RATE = 0.0003        # 0.03%
BROKERAGE_CAP = 20.0           # ₹20 per executed order
STT_RATE = 0.0002              # 0.02%, sell side only
EXCHANGE_TXN_RATE = 0.000019   # NSE F&O, both sides
SEBI_RATE = 0.000001           # ₹10/crore, both sides
STAMP_DUTY_RATE = 0.00002      # 0.002%, buy side only
GST_RATE = 0.18


def calculate_futures_costs(buy_price: float, sell_price: float, qty: int) -> dict:
    """
    qty = total units (lots x lot_size), not number of lots.
    For a SHORT trade, pass buy_price = exit price and sell_price = entry
    price (sell happens first) so the STT/stamp-duty legs land correctly.
    """
    buy_turnover = buy_price * qty
    sell_turnover = sell_price * qty
    total_turnover = buy_turnover + sell_turnover

    brokerage_buy = min(BROKERAGE_CAP, BROKERAGE_RATE * buy_turnover)
    brokerage_sell = min(BROKERAGE_CAP, BROKERAGE_RATE * sell_turnover)
    brokerage = brokerage_buy + brokerage_sell

    stt = STT_RATE * sell_turnover
    exchange_txn = EXCHANGE_TXN_RATE * total_turnover
    sebi_fee = SEBI_RATE * total_turnover
    stamp_duty = STAMP_DUTY_RATE * buy_turnover
    gst = GST_RATE * (brokerage + exchange_txn + sebi_fee)

    total_charges = brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst

    return {
        "buy_turnover": round(buy_turnover, 2),
        "sell_turnover": round(sell_turnover, 2),
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_txn_charges": round(exchange_txn, 2),
        "sebi_charges": round(sebi_fee, 2),
        "stamp_duty": round(stamp_duty, 2),
        "gst": round(gst, 2),
        "total_charges": round(total_charges, 2),
    }


if __name__ == "__main__":
    example = calculate_futures_costs(24800, 24850, 25)
    for k, v in example.items():
        print(f"{k:>22}: {v}")
