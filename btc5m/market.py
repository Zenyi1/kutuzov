import time
import json
import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from btc5m.config import (
    POLYMARKET_API_KEY, PRIVATE_KEY, CLOB_API_URL, GAMMA_API_URL,
    DRY_RUN, ENTRY_LOW, ENTRY_HIGH, ARB_THRESHOLD, MIN_BOOK_SIZE,
)


def current_window_ts():
    now = int(time.time())
    return now - (now % 300)


def next_window_ts():
    return current_window_ts() + 300


def seconds_into_window():
    now = time.time()
    window = now - (now % 300)
    return now - window


def seconds_until_next_window():
    return 300 - seconds_into_window()


def fetch_5m_market(timestamp):
    """fetch btc 5m up/down market by timestamp slug"""
    slug = f"btc-updown-5m-{timestamp}"
    try:
        r = requests.get(
            f"{GAMMA_API_URL}/events",
            params={"slug": slug},
            timeout=10,
        )
        events = r.json()
        if not events:
            return None

        event = events[0] if isinstance(events, list) else events
        markets = event.get("markets", [])
        if not markets:
            return None

        result = {
            "slug": slug,
            "title": event.get("title", ""),
            "end_date": event.get("endDate", ""),
            "up_token_id": None,
            "down_token_id": None,
            "up_price": 0.0,
            "down_price": 0.0,
        }

        for m in markets:
            if not m.get("active"):
                continue

            outcomes = json.loads(m.get("outcomes", "[]"))
            prices = json.loads(m.get("outcomePrices", "[]"))
            tokens = json.loads(m.get("clobTokenIds", "[]"))

            for i, outcome in enumerate(outcomes):
                name = outcome.lower()
                if i < len(prices) and i < len(tokens):
                    price = float(prices[i])
                    token = tokens[i]
                    if "up" in name or "yes" in name:
                        result["up_token_id"] = token
                        result["up_price"] = price
                    elif "down" in name or "no" in name:
                        result["down_token_id"] = token
                        result["down_price"] = price

        if not result["up_token_id"] and not result["down_token_id"]:
            return None

        return result
    except Exception as e:
        print(f"  fetch market error: {e}")
        return None


def get_cheap_side(market):
    """return (side_name, token_id, current_price) for whichever side is cheaper"""
    up = market["up_price"]
    down = market["down_price"]

    if down < up and market["down_token_id"]:
        return "down", market["down_token_id"], down
    elif market["up_token_id"]:
        return "up", market["up_token_id"], up
    return None


def check_arb(market):
    """check if both sides sum below arb threshold"""
    total = market["up_price"] + market["down_price"]
    return total < ARB_THRESHOLD and total > 0


def get_client():
    client = ClobClient(CLOB_API_URL, key=POLYMARKET_API_KEY, chain_id=137, funder=PRIVATE_KEY)
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def get_book_depth(client, token_id, max_price):
    """sum ask sizes at or below max_price"""
    try:
        book = client.get_order_book(token_id)
        total = 0.0
        for ask in (book.asks or []):
            if float(ask.price) <= max_price:
                total += float(ask.size)
        return total
    except Exception as e:
        print(f"  order book error: {e}")
        return 0.0


def place_gtd_buy(client, token_id, price, size, expiration):
    """place a GTD limit buy that auto-expires"""
    if DRY_RUN:
        print(f"  [dry run] GTD BUY {size:.1f} shares @ {price:.3f} (expires {expiration})")
        return {"dry_run": True}

    signed = client.create_order(OrderArgs(
        token_id=token_id, price=price, size=size,
        side="BUY", expiration=expiration,
    ))
    result = client.post_order(signed, OrderType.GTD)
    print(f"  placed GTD BUY: {result}")
    return result


def place_sell(client, token_id, price, size):
    """place a GTC take-profit sell"""
    if DRY_RUN:
        print(f"  [dry run] GTC SELL {size:.1f} shares @ {price:.3f}")
        return {"dry_run": True}

    signed = client.create_order(OrderArgs(
        token_id=token_id, price=price, size=size, side="SELL",
    ))
    result = client.post_order(signed, OrderType.GTC)
    print(f"  placed SELL: {result}")
    return result


def place_market_sell(client, token_id, size, min_price):
    """aggressive exit via FOK at min acceptable price"""
    if DRY_RUN:
        print(f"  [dry run] FOK SELL {size:.1f} shares @ {min_price:.3f}")
        return {"dry_run": True}

    signed = client.create_order(OrderArgs(
        token_id=token_id, price=min_price, size=size, side="SELL",
    ))
    result = client.post_order(signed, OrderType.FOK)
    print(f"  FOK SELL: {result}")
    return result


def cancel_order(client, order_id):
    """cancel an open order"""
    if DRY_RUN:
        print(f"  [dry run] cancel order {order_id}")
        return
    try:
        client.cancel(order_id)
        print(f"  cancelled order {order_id}")
    except Exception as e:
        print(f"  cancel error: {e}")
