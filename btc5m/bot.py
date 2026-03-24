import time
from btc5m.config import (
    BUDGET, ENTRY_LOW, ENTRY_HIGH, TP_PRICE, TP_CLOSE, SWING_BPS,
    ARB_THRESHOLD, MIN_BOOK_SIZE, ENTRY_WINDOW, EXIT_START, DRY_RUN,
)
from btc5m.price import get_btc_price, price_change_bps, clear_history
from btc5m import market
from btc5m import paper


def run_window(skipping):
    """run one 5-minute trading window. returns swing in bps."""
    window_ts = market.next_window_ts()
    wait = market.seconds_until_next_window()

    print(f"\n--- window {window_ts} starts in {wait:.0f}s ---")
    time.sleep(max(0, wait))

    #record btc price at window open
    clear_history()
    time.sleep(1)
    btc_open = get_btc_price()
    if btc_open == 0:
        print("  no btc price available, skipping")
        return 0

    print(f"  btc open: ${btc_open:,.2f}")

    #fetch market
    mkt = market.fetch_5m_market(window_ts)
    if not mkt:
        print("  market not found, observing only")
        time.sleep(290)
        return _measure_swing(btc_open)

    print(f"  up: {mkt['up_price']:.3f}  down: {mkt['down_price']:.3f}  sum: {mkt['up_price'] + mkt['down_price']:.3f}")

    #arb check (always runs, even when skipping)
    if market.check_arb(mkt):
        print(f"  ARB DETECTED: sum = {mkt['up_price'] + mkt['down_price']:.3f}")
        _execute_arb(mkt, window_ts)
        time.sleep(max(0, 300 - market.seconds_into_window()))
        swing = _measure_swing(btc_open)
        paper.log_arb(window_ts, mkt["up_price"], mkt["down_price"], BUDGET, btc_open, get_btc_price(), swing)
        return swing

    #skip check
    if skipping:
        print("  skipping (volatile market)")
        time.sleep(max(0, 300 - market.seconds_into_window()))
        swing = _measure_swing(btc_open)
        paper.log_skip(window_ts, "volatile", btc_open, get_btc_price(), swing)
        return swing

    #entry phase
    position = _entry_phase(mkt, window_ts, btc_open)

    if not position:
        #no fill, wait for window end
        remaining = 300 - market.seconds_into_window()
        if remaining > 0:
            time.sleep(remaining)
        swing = _measure_swing(btc_open)
        paper.log_skip(window_ts, "no entry", btc_open, get_btc_price(), swing)
        return swing

    #monitor + exit phases
    _monitor_and_exit(position, btc_open)

    return _measure_swing(btc_open)


def _entry_phase(mkt, window_ts, btc_open):
    """place limit buy on cheaper side at ENTRY_HIGH, let GTD handle fill"""
    cheap = market.get_cheap_side(mkt)
    if not cheap:
        print(f"  no market tokens available")
        return None

    side, token_id, current_price = cheap
    buy_price = ENTRY_HIGH  #limit price we're willing to pay
    size = round(BUDGET / buy_price, 1)
    expiration = window_ts + ENTRY_WINDOW

    print(f"  {side} currently @ {current_price:.3f}, placing limit buy @ {buy_price:.3f} for {size} shares (expires 2min)")

    #check order book depth
    client = market.get_client() if not DRY_RUN else None

    if client:
        depth = market.get_book_depth(client, token_id, ENTRY_HIGH)
        print(f"  book depth at {ENTRY_HIGH}: {depth:.1f} shares")

    #place GTD buy
    result = market.place_gtd_buy(
        client, token_id, buy_price, size, expiration,
    )

    if DRY_RUN:
        return {
            "side": side,
            "token_id": token_id,
            "entry_price": buy_price,
            "size": size,
            "cost": round(size * buy_price, 2),
            "tp_order_id": None,
            "client": None,
        }

    #poll for fill
    order_id = result.get("orderID") or result.get("id")
    if not order_id:
        print(f"  no order id returned: {result}")
        return None

    filled_size = _poll_fill(client, order_id, window_ts + ENTRY_WINDOW)
    if filled_size <= 0:
        print("  order expired unfilled")
        return None

    #place take-profit sell
    tp_result = market.place_sell(client, token_id, TP_PRICE, filled_size)
    tp_order_id = None
    if tp_result:
        tp_order_id = tp_result.get("orderID") or tp_result.get("id")

    return {
        "side": side,
        "token_id": token_id,
        "entry_price": buy_price,
        "size": filled_size,
        "cost": round(filled_size * buy_price, 2),
        "tp_order_id": tp_order_id,
        "client": client,
    }


def _poll_fill(client, order_id, deadline):
    """poll order status until filled or deadline"""
    while time.time() < deadline:
        try:
            order = client.get_order(order_id)
            status = order.get("status", "")
            size_matched = float(order.get("size_matched", 0))

            if status == "MATCHED" or size_matched > 0:
                print(f"  filled: {size_matched} shares")
                return size_matched
            if status in ("CANCELLED", "EXPIRED"):
                return 0
        except Exception:
            pass
        time.sleep(5)
    return 0


def _monitor_and_exit(position, btc_open):
    """monitor position and handle exit"""
    client = position["client"]
    side = position["side"]
    token_id = position["token_id"]
    tp_order_id = position["tp_order_id"]
    window_ts = market.current_window_ts()

    while True:
        elapsed = market.seconds_into_window()
        remaining = 300 - elapsed

        if remaining <= 0:
            break

        #check if take-profit filled (live only)
        if client and tp_order_id:
            try:
                order = client.get_order(tp_order_id)
                if order.get("status") == "MATCHED":
                    profit = round(position["size"] * TP_PRICE - position["cost"], 2)
                    print(f"  TAKE PROFIT: +${profit}")
                    btc_close = get_btc_price()
                    swing = price_change_bps(btc_open, btc_close)
                    paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "TP WIN", profit, btc_open, btc_close, swing)
                    return
            except Exception:
                pass

        #check if price is close to TP — market sell to lock in profit
        fresh = market.fetch_5m_market(window_ts)
        if fresh:
            current = fresh["up_price"] if side == "up" else fresh["down_price"]
            if current >= TP_PRICE - TP_CLOSE and current > position["entry_price"]:
                profit = round(position["size"] * current - position["cost"], 2)
                print(f"  CLOSE ENOUGH: {side} @ {current:.3f} (tp={TP_PRICE}), selling +${profit}")
                if client:
                    if tp_order_id:
                        market.cancel_order(client, tp_order_id)
                    market.place_market_sell(client, token_id, position["size"], current - 0.02)
                btc_close = get_btc_price()
                swing = price_change_bps(btc_open, btc_close)
                paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "TP CLOSE", profit, btc_open, btc_close, swing)
                return

        #exit phase: stop-loss check in last minute
        if elapsed >= EXIT_START:
            btc_now = get_btc_price()
            move_bps = price_change_bps(btc_open, btc_now)
            btc_went_up = btc_now > btc_open

            #check if move is against our side
            against = (side == "up" and not btc_went_up) or (side == "down" and btc_went_up)

            if against and move_bps > SWING_BPS:
                print(f"  STOP LOSS: btc moved {move_bps:.1f} bps against {side}")
                #in paper mode, assume we salvage ~5% of position
                salvage = round(position["size"] * 0.05, 2)
                loss = round(salvage - position["cost"], 2)
                if client:
                    if tp_order_id:
                        market.cancel_order(client, tp_order_id)
                    market.place_market_sell(client, token_id, position["size"], 0.01)
                btc_close = get_btc_price()
                swing = price_change_bps(btc_open, btc_close)
                paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "STOP LOSS", loss, btc_open, btc_close, swing)
                return

        time.sleep(5)

    #window ended, position settles
    btc_close = get_btc_price()
    btc_went_up = btc_close > btc_open
    won = (side == "up" and btc_went_up) or (side == "down" and not btc_went_up)
    swing = price_change_bps(btc_open, btc_close)

    if won:
        payout = position["size"]
        profit = round(payout - position["cost"], 2)
        print(f"  SETTLED WIN: payout ${payout:.2f}, profit +${profit}")
        paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "WIN", profit, btc_open, btc_close, swing)
    else:
        loss = -position["cost"]
        print(f"  SETTLED LOSS: -${position['cost']}")
        paper.log_trade(window_ts, side, position["entry_price"], position["size"], position["cost"], "LOSS", loss, btc_open, btc_close, swing)


def _execute_arb(mkt, window_ts):
    """buy both sides for guaranteed profit"""
    total_price = mkt["up_price"] + mkt["down_price"]
    profit_pct = (1.0 - total_price) / total_price * 100
    print(f"  arb profit: {profit_pct:.1f}%")

    client = market.get_client() if not DRY_RUN else None
    expiration = window_ts + ENTRY_WINDOW

    #split budget proportional to price
    up_alloc = BUDGET * (mkt["up_price"] / total_price)
    down_alloc = BUDGET * (mkt["down_price"] / total_price)

    up_size = round(up_alloc / mkt["up_price"], 1)
    down_size = round(down_alloc / mkt["down_price"], 1)

    if mkt["up_token_id"]:
        market.place_gtd_buy(client, mkt["up_token_id"], mkt["up_price"], up_size, expiration)
    if mkt["down_token_id"]:
        market.place_gtd_buy(client, mkt["down_token_id"], mkt["down_price"], down_size, expiration)


def _measure_swing(btc_open):
    btc_close = get_btc_price()
    if btc_open == 0 or btc_close == 0:
        return 0
    swing = price_change_bps(btc_open, btc_close)
    print(f"  window swing: {swing:.1f} bps (${btc_close:,.2f})")
    return swing
