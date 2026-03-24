import time
from btc5m.config import (
    BUDGET, ENTRY_LOW, ENTRY_HIGH, TP_PRICE, SWING_BPS,
    ARB_THRESHOLD, MIN_BOOK_SIZE, ENTRY_WINDOW, EXIT_START, DRY_RUN,
)
from btc5m.price import get_btc_price, price_change_bps, clear_history
from btc5m import market


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
        return _measure_swing(btc_open)

    #skip check
    if skipping:
        print("  skipping (volatile market)")
        time.sleep(max(0, 300 - market.seconds_into_window()))
        return _measure_swing(btc_open)

    #entry phase
    position = _entry_phase(mkt, window_ts, btc_open)

    if not position:
        #no fill, wait for window end
        remaining = 300 - market.seconds_into_window()
        if remaining > 0:
            time.sleep(remaining)
        return _measure_swing(btc_open)

    #monitor + exit phases
    _monitor_and_exit(position, btc_open)

    return _measure_swing(btc_open)


def _entry_phase(mkt, window_ts, btc_open):
    """try to enter a position in the first ENTRY_WINDOW seconds"""
    cheap = market.get_cheap_side(mkt)
    if not cheap:
        print(f"  no side in range [{ENTRY_LOW}-{ENTRY_HIGH}]")
        return None

    side, token_id, price = cheap
    print(f"  target: {side} @ {price:.3f}")

    #check order book depth
    client = market.get_client() if not DRY_RUN else None

    if client:
        depth = market.get_book_depth(client, token_id, ENTRY_HIGH)
        if depth < MIN_BOOK_SIZE:
            print(f"  book depth {depth:.1f} < {MIN_BOOK_SIZE}, skipping")
            return None
        print(f"  book depth: {depth:.1f} shares")

    #compute size
    size = round(BUDGET / price, 1)
    expiration = window_ts + ENTRY_WINDOW

    #place GTD buy
    result = market.place_gtd_buy(
        client, token_id, price, size, expiration,
    )

    if DRY_RUN:
        #simulate fill for dry run
        return {
            "side": side,
            "token_id": token_id,
            "entry_price": price,
            "size": size,
            "cost": round(size * price, 2),
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
        "entry_price": price,
        "size": filled_size,
        "cost": round(filled_size * price, 2),
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

    while True:
        elapsed = market.seconds_into_window()
        remaining = 300 - elapsed

        if remaining <= 0:
            break

        #check if take-profit filled
        if client and tp_order_id:
            try:
                order = client.get_order(tp_order_id)
                if order.get("status") == "MATCHED":
                    profit = round(position["size"] * TP_PRICE - position["cost"], 2)
                    print(f"  TAKE PROFIT: +${profit}")
                    return
            except Exception:
                pass

        #exit phase: stop-loss check in last minute
        if elapsed >= EXIT_START:
            btc_now = get_btc_price()
            move_bps = price_change_bps(btc_open, btc_now)
            btc_went_up = btc_now > btc_open

            #check if move is against our side
            against = (side == "up" and not btc_went_up) or (side == "down" and btc_went_up)

            if against and move_bps > SWING_BPS:
                print(f"  STOP LOSS: btc moved {move_bps:.1f} bps against {side}")
                if client:
                    if tp_order_id:
                        market.cancel_order(client, tp_order_id)
                    market.place_market_sell(client, token_id, position["size"], 0.01)
                else:
                    print(f"  [dry run] would cancel TP and FOK sell at 0.01")
                return

        if DRY_RUN and remaining < 295:
            #don't sit in dry run for 5 min
            btc_now = get_btc_price()
            move = price_change_bps(btc_open, btc_now)
            print(f"  [dry run] {remaining:.0f}s left, btc move: {move:.1f} bps")
            break

        time.sleep(5)

    #window ended, position settles
    btc_close = get_btc_price()
    btc_went_up = btc_close > btc_open
    won = (side == "up" and btc_went_up) or (side == "down" and not btc_went_up)

    if won:
        payout = position["size"]
        profit = round(payout - position["cost"], 2)
        print(f"  SETTLED WIN: payout ${payout:.2f}, profit +${profit}")
    else:
        print(f"  SETTLED LOSS: -${position['cost']}")


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
