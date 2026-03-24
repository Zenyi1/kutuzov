import time
from btc5m.config import (
    BUDGET, ENTRY_LOW, ENTRY_HIGH, TP_PRICE, SWING_BPS,
    SKIP_BPS, CALM_BPS, ENTRY_WINDOW, EXIT_START,
)
from btc5m.price import start_price_feed, get_btc_price, price_change_bps, clear_history, is_connected
from btc5m import market
from btc5m import paper


def run():
    start_price_feed()
    print("waiting for price feed...")
    for _ in range(10):
        if is_connected():
            break
        time.sleep(1)
    if not is_connected():
        print("could not connect to price feed")
        return

    print("paper trading -- logging to paper_trades.txt")
    print(f"  entry range: [{ENTRY_LOW}-{ENTRY_HIGH}]")
    print(f"  take profit: {TP_PRICE}")
    print(f"  budget: ${BUDGET}")
    print(f"  skip/calm/swing bps: {SKIP_BPS}/{CALM_BPS}/{SWING_BPS}")

    skipping = False
    windows = 0

    while True:
        swing_bps = _run_window(skipping)
        windows += 1

        if swing_bps > SKIP_BPS:
            skipping = True
            print(f"volatile ({swing_bps:.1f} bps) -- skipping until calm")
        elif skipping and swing_bps < CALM_BPS:
            skipping = False
            print(f"calm ({swing_bps:.1f} bps) -- resuming")
        elif skipping:
            print(f"still volatile ({swing_bps:.1f} bps)")

        if windows % 12 == 0:
            paper.summary()


def _run_window(skipping):
    """paper trade one 5-minute window"""
    window_ts = market.next_window_ts()
    wait = market.seconds_until_next_window()

    print(f"\n--- window {window_ts} in {wait:.0f}s ---")
    time.sleep(max(0, wait))

    clear_history()
    time.sleep(1)
    btc_open = get_btc_price()
    if btc_open == 0:
        print("  no price")
        return 0

    print(f"  btc: ${btc_open:,.2f}")

    #fetch initial market
    mkt = market.fetch_5m_market(window_ts)
    if not mkt:
        print("  market not found")
        time.sleep(290)
        return _swing(btc_open)

    print(f"  up: {mkt['up_price']:.3f}  down: {mkt['down_price']:.3f}")

    #arb check
    if market.check_arb(mkt):
        total = mkt["up_price"] + mkt["down_price"]
        pnl = round(BUDGET / total - BUDGET, 2)
        print(f"  ARB: sum={total:.3f} pnl=+${pnl}")
        time.sleep(max(0, 300 - market.seconds_into_window()))
        swing = _swing(btc_open)
        paper.log_arb(window_ts, mkt["up_price"], mkt["down_price"], BUDGET, btc_open, get_btc_price(), swing)
        return swing

    #skip check
    if skipping:
        print("  skipping (volatile)")
        time.sleep(max(0, 300 - market.seconds_into_window()))
        swing = _swing(btc_open)
        paper.log_skip(window_ts, "volatile", btc_open, get_btc_price(), swing)
        return swing

    #entry phase: poll market every 10s for 2 minutes looking for cheap side
    print(f"  watching [{ENTRY_LOW}-{ENTRY_HIGH}] for {ENTRY_WINDOW}s...")
    deadline = window_ts + ENTRY_WINDOW
    position = None

    while time.time() < deadline:
        fresh = market.fetch_5m_market(window_ts)
        if fresh:
            cheap = market.get_cheap_side(fresh)
            if cheap:
                side, token_id, price = cheap
                size = round(BUDGET / price, 1)
                cost = round(size * price, 2)
                print(f"  FILL: {side} @ {price:.3f}, {size} shares, ${cost}")
                position = {"side": side, "entry_price": price, "size": size, "cost": cost}
                break
        time.sleep(10)

    if not position:
        print(f"  no fill in entry window")
        remaining = 300 - market.seconds_into_window()
        if remaining > 0:
            time.sleep(remaining)
        swing = _swing(btc_open)
        paper.log_skip(window_ts, "no fill", btc_open, get_btc_price(), swing)
        return swing

    #monitor phase: wait for window end, check stop-loss
    side = position["side"]
    while True:
        elapsed = market.seconds_into_window()
        remaining = 300 - elapsed
        if remaining <= 0:
            break

        btc_now = get_btc_price()
        move_bps = price_change_bps(btc_open, btc_now)

        #check take-profit: re-fetch market to see if our side price hit TP
        if elapsed % 15 < 6:
            fresh = market.fetch_5m_market(window_ts)
            if fresh:
                if side == "up":
                    current_price = fresh["up_price"]
                else:
                    current_price = fresh["down_price"]
                if current_price >= TP_PRICE:
                    profit = round(position["size"] * TP_PRICE - position["cost"], 2)
                    print(f"  TAKE PROFIT: {side} hit {current_price:.3f} >= {TP_PRICE}, +${profit}")
                    swing = _swing(btc_open)
                    paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "TP WIN", profit, btc_open, get_btc_price(), swing)
                    #wait for window end
                    time.sleep(max(0, remaining))
                    return swing

        #stop-loss in final minute
        if elapsed >= EXIT_START:
            btc_went_up = btc_now > btc_open
            against = (side == "up" and not btc_went_up) or (side == "down" and btc_went_up)
            if against and move_bps > SWING_BPS:
                salvage = round(position["size"] * 0.05, 2)
                loss = round(salvage - position["cost"], 2)
                print(f"  STOP LOSS: {move_bps:.1f} bps against {side}, loss=${loss}")
                swing = _swing(btc_open)
                paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "STOP LOSS", loss, btc_open, get_btc_price(), swing)
                time.sleep(max(0, remaining))
                return swing

        time.sleep(5)

    #window settled
    btc_close = get_btc_price()
    btc_went_up = btc_close > btc_open
    won = (side == "up" and btc_went_up) or (side == "down" and not btc_went_up)
    swing = price_change_bps(btc_open, btc_close)

    if won:
        payout = position["size"]
        profit = round(payout - position["cost"], 2)
        print(f"  WIN: ${payout:.2f} payout, +${profit}")
        paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "WIN", profit, btc_open, btc_close, swing)
    else:
        print(f"  LOSS: -${position['cost']}")
        paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "LOSS", -position["cost"], btc_open, btc_close, swing)

    return swing


def _swing(btc_open):
    btc_close = get_btc_price()
    if btc_open == 0 or btc_close == 0:
        return 0
    s = price_change_bps(btc_open, btc_close)
    print(f"  swing: {s:.1f} bps (${btc_close:,.2f})")
    return s


if __name__ == "__main__":
    run()
