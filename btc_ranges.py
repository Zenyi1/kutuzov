import json
import sys
import re
import math
import requests
import numpy as np
import yfinance as yf
from scipy.stats import norm
from datetime import datetime, timezone
from config import GAMMA_API_URL, BET_BUDGET


def fetch_btc_events():
    """find all open 'Bitcoin price on' daily events"""
    events = []
    offset = 0
    while True:
        resp = requests.get(
            f"{GAMMA_API_URL}/events",
            params={"closed": "false", "limit": 100, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for e in batch:
            if "bitcoin price on" in e.get("title", "").lower():
                events.append(e)
        if len(batch) < 100:
            break
        offset += 100
    return sorted(events, key=lambda e: e.get("endDate", ""))


def parse_range(question):
    q = question.lower().replace(",", "")
    m = re.search(r"between \$(\d+) and \$(\d+)", q)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"greater than \$(\d+)", q)
    if m:
        return int(m.group(1)), int(m.group(1)) + 100000
    m = re.search(r"less than \$(\d+)", q)
    if m:
        return 0, int(m.group(1))
    return None


def format_range(low, high):
    if low == 0:
        return f"<${high:,}"
    if high >= 100000:
        return f">${low:,}"
    return f"${low:,}-${high:,}"


def parse_event_markets(event):
    markets = []
    for m in event.get("markets", []):
        if not m.get("active"):
            continue
        op = m.get("outcomePrices")
        if not op:
            continue
        try:
            price = float(json.loads(op)[0])
        except (json.JSONDecodeError, IndexError, ValueError):
            continue
        if price <= 0.001:
            continue
        r = parse_range(m.get("question", ""))
        if not r:
            continue
        markets.append({"low": r[0], "high": r[1], "price": price})

    markets.sort(key=lambda x: x["low"])
    return markets


def get_btc_volatility(lookback_days=90):
    btc = yf.download("BTC-USD", period=f"{lookback_days}d", interval="1d", progress=False)
    closes = btc["Close"].values.flatten()
    log_returns = np.diff(np.log(closes))
    return closes[-1], np.mean(log_returns), np.std(log_returns)


def model_probabilities(current_price, mu, sigma, markets, days_ahead):
    log_price = math.log(current_price)
    drift = mu * days_ahead
    vol = sigma * math.sqrt(days_ahead)

    for m in markets:
        low, high = m["low"], m["high"]
        if low == 0:
            m["model"] = norm.cdf((math.log(high) - log_price - drift) / vol)
        elif high >= 100000:
            m["model"] = 1.0 - norm.cdf((math.log(low) - log_price - drift) / vol)
        else:
            z_lo = (math.log(low) - log_price - drift) / vol
            z_hi = (math.log(high) - log_price - drift) / vol
            m["model"] = norm.cdf(z_hi) - norm.cdf(z_lo)

    return markets


def compute_bets(markets, budget):
    """allocate budget proportional to price across top-k ranges that sum < 1"""
    by_price = sorted(markets, key=lambda x: x["price"], reverse=True)

    for k in range(2, min(5, len(by_price) + 1)):
        top_k = by_price[:k]
        price_sum = sum(m["price"] for m in top_k)
        if price_sum < 1.0:
            payout = budget / price_sum
            bets = []
            for m in top_k:
                amount = budget * m["price"] / price_sum
                bets.append({
                    "range": format_range(m["low"], m["high"]),
                    "price": m["price"],
                    "model": m.get("model", 0),
                    "bet": round(amount, 2),
                    "shares": round(amount / m["price"], 2),
                })
            return "arbitrage", price_sum, bets

    return None, 0, []


def main():
    print("fetching btc data...", file=sys.stderr)
    current_price, mu, sigma = get_btc_volatility()
    events = fetch_btc_events()

    #skip events ending in < 2 hours (already settled)
    now = datetime.now(timezone.utc)
    active = []
    for e in events:
        try:
            end = datetime.fromisoformat(e.get("endDate", "").replace("Z", "+00:00"))
            if (end - now).total_seconds() > 7200:
                active.append(e)
        except (ValueError, AttributeError):
            continue

    if not active:
        print("no active btc daily events found")
        return

    print(f"\nBTC: ${current_price:,.0f} | daily vol: {sigma:.1%} | budget: ${BET_BUDGET}\n")

    for event in active:
        markets = parse_event_markets(event)
        if not markets:
            continue

        end = datetime.fromisoformat(event["endDate"].replace("Z", "+00:00"))
        days_ahead = max(0.5, (end - now).total_seconds() / 86400)
        markets = model_probabilities(current_price, mu, sigma, markets, days_ahead)

        strategy, price_sum, bets = compute_bets(markets, BET_BUDGET)

        print(f"--- {event['title']} ({days_ahead:.1f}d ahead) ---")
        print()

        #show all ranges
        print(f"  {'range':>20s}  market  model")
        for m in sorted(markets, key=lambda x: x["price"], reverse=True):
            marker = " *" if any(b["range"] == format_range(m["low"], m["high"]) for b in bets) else ""
            print(f"  {format_range(m['low'], m['high']):>20s}  {m['price']:5.1%}  {m.get('model',0):5.1%}{marker}")

        print()
        if strategy == "arbitrage":
            payout = BET_BUDGET / price_sum
            model_coverage = sum(b["model"] for b in bets)
            print(f"  arb: top {len(bets)} sum to {price_sum:.4f}")
            print(f"  payout: ${payout:.2f} | profit: ${payout - BET_BUDGET:.2f} | model coverage: {model_coverage:.0%}")
            print()
            print(f"  {'range':>20s}  {'price':>6s}  {'bet':>7s}  shares")
            for b in bets:
                print(f"  {b['range']:>20s}  {b['price']:5.1%}  ${b['bet']:6.2f}  {b['shares']:.1f}")
        else:
            print("  no arb available")

        print()


if __name__ == "__main__":
    main()
