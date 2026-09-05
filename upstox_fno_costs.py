"""
Upstox NSE index-FUTURES charges calculator (paper trading, for projection
purposes only -- you're not actually paying these, this just tells you what
the trade would have cost on a real Upstox account).

This project trades NIFTY/BANKNIFTY as a plain points-times-lot-size payoff
(no strike selection, no premium decay), which mechanically matches an
INDEX FUTURES position, not an option or delivery equity trade -- futures
rates apply throughout.

Rates as given by the user, verified current 2026-09-05:

  API brokerage    : Rs 10 flat per executed order (promotional rate valid
                      until 30-Sep-2026, excludes API GTT orders -- recheck
                      after that date, it may revert to a higher rate)
  STT               : 0.05% on SELL turnover only (futures rate; delivery/
                      intraday-equity/options rates differ and don't apply)
  Exchange (NSE)
  transaction chg   : 0.00183% on turnover, both sides (futures rate)
  SEBI turnover fee : Rs 10 per crore (0.0001%), both sides
  Stamp duty        : 0.002% on BUY turnover only (futures rate)
  GST               : 18% on (brokerage + exchange txn charges) -- per the
                      rate card this is GST on "brokerage, transaction,
                      demat and applicable IPFT charges"; demat/IPFT don't
                      apply to a futures-style position here, so just those
                      two. Note this excludes SEBI fee and STT from the GST
                      base, unlike the old (Zerodha-based) cost model this
                      file replaced.
  Delivery sale
  DP charge         : Rs 20 + GST per scrip per day -- NOT applicable here,
                      this only applies to delivery/holdings sales, not a
                      futures-style intraday position.
"""

BROKERAGE_PER_ORDER = 10.0     # Rs 10 flat, per executed order (buy and sell each count)
STT_RATE = 0.0005              # 0.05%, futures, sell side only
EXCHANGE_TXN_RATE = 0.0000183  # 0.00183%, futures, both sides
SEBI_RATE = 0.000001           # Rs 10/crore, both sides
STAMP_DUTY_RATE = 0.00002      # 0.002%, futures, buy side only
GST_RATE = 0.18                # on brokerage + exchange txn charges only


def calculate_futures_costs(buy_price: float, sell_price: float, qty: int) -> dict:
    """
    qty = total units (lots x lot_size), not number of lots.
    For a SHORT trade, pass buy_price = exit price and sell_price = entry
    price (sell happens first) so the STT/stamp-duty legs land correctly.
    """
    buy_turnover = buy_price * qty
    sell_turnover = sell_price * qty
    total_turnover = buy_turnover + sell_turnover

    brokerage = BROKERAGE_PER_ORDER * 2  # one order to open, one to close
    stt = STT_RATE * sell_turnover
    exchange_txn = EXCHANGE_TXN_RATE * total_turnover
    sebi_fee = SEBI_RATE * total_turnover
    stamp_duty = STAMP_DUTY_RATE * buy_turnover
    gst = GST_RATE * (brokerage + exchange_txn)

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
