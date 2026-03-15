import json
import os
from datetime import datetime, timezone
from discovery import get_market_prices, fetch_events
from notifier import notify_price_shift, notify_resolution

POSITIONS_FILE = "positions.json"
PRICE_SHIFT_THRESHOLD = 0.05  #5% change triggers alert


def load_positions():
    """load tracked positions from disk"""
    if not os.path.exists(POSITIONS_FILE):
        return []
    with open(POSITIONS_FILE, "r") as f:
        return json.load(f)


def save_positions(positions):
    """save tracked positions to disk"""
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


def add_position(opportunity, bets):
    """record a new position after execution"""
    positions = load_positions()
    position = {
        "event_id": opportunity["event_id"],
        "event_title": opportunity["event_title"],
        "end_date": opportunity["end_date"],
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "total_spent": sum(b["amount"] for b in bets),
        "candidates": [],
    }

    for bet in bets:
        position["candidates"].append({
            "question": bet["question"],
            "yes_token_id": bet["yes_token_id"],
            "entry_price": bet["price"],
            "current_price": bet["price"],
            "amount_spent": bet["amount"],
            "shares": bet["expected_shares"],
        })

    positions.append(position)
    save_positions(positions)
    return position


def check_positions():
    """check all open positions for price changes and resolutions"""
    positions = load_positions()
    if not positions:
        print("[monitor] no open positions")
        return

    events = fetch_events()
    events_by_id = {str(e.get("id")): e for e in events}
    updated = []

    for pos in positions:
        event = events_by_id.get(str(pos["event_id"]))

        #check if event is resolved (no longer in open events)
        if event is None:
            print(f"[monitor] event resolved or removed: {pos['event_title']}")
            #can't determine pnl without resolution data, just notify
            notify_resolution(pos["event_title"], 0)
            continue

        #check price changes
        current_prices = get_market_prices(event)
        price_map = {}
        for outcome in current_prices:
            price_map[outcome.get("yes_token_id")] = outcome["price"]

        position_changed = False
        for candidate in pos["candidates"]:
            token_id = candidate.get("yes_token_id")
            if token_id and token_id in price_map:
                new_price = price_map[token_id]
                old_price = candidate["current_price"]

                if old_price > 0:
                    change = abs(new_price - old_price) / old_price
                    if change >= PRICE_SHIFT_THRESHOLD:
                        notify_price_shift(
                            pos["event_title"],
                            candidate["question"],
                            old_price,
                            new_price,
                        )
                        position_changed = True

                candidate["current_price"] = new_price

        updated.append(pos)

        if position_changed:
            #recalculate position health
            price_sum = sum(c["current_price"] for c in pos["candidates"])
            print(f"[monitor] {pos['event_title']}: current price sum = {price_sum:.4f}")

    save_positions(updated)
    print(f"[monitor] checked {len(updated)} positions")
