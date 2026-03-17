import json
import sys
import re
import math
import requests
import numpy as np
import yfinance as yf
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
    """extract (low, high) from market question, returns (low, high) or None.
    handles 'between $X and $Y', 'greater than $X', 'less than $X'
    """
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


def parse_event_markets(event):
    """extract ranges with prices and token ids from a btc daily event"""
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

        r = parse_range(m.get("question", ""))
        if not r:
            continue

        token_id = None
        tids = m.get("clobTokenIds")
        if tids:
            try:
                ids = json.loads(tids) if isinstance(tids, str) else tids
                token_id = ids[0] if ids else None
            except (json.JSONDecodeError, IndexError):
                pass

        markets.append({
            "question": m["question"],
            "low": r[0],
            "high": r[1],
            "market_price": price,
            "yes_token_id": token_id,
        })

    markets.sort(key=lambda x: x["low"])
    return markets


def get_btc_volatility(lookback_days=90):
    """compute daily log-return std from recent BTC history"""
    btc = yf.download("BTC-USD", period=f"{lookback_days}d", interval="1d", progress=False)
    closes = btc["Close"].values.flatten()
    current_price = closes[-1]
    log_returns = np.diff(np.log(closes))
    mu = np.mean(log_returns)
    sigma = np.std(log_returns)
    return current_price, mu, sigma


def model_range_probabilities(current_price, mu, sigma, markets, days_ahead=1):
    """compute our model probability for each range bucket using log-normal distribution.
    price at resolution ~ LogNormal(ln(current) + mu*days, sigma*sqrt(days))
    """
    from scipy.stats import norm

    log_price = math.log(current_price)
    drift = mu * days_ahead
    vol = sigma * math.sqrt(days_ahead)

    for m in markets:
        low, high = m["low"], m["high"]
        if low == 0:
            #less than X
            z = (math.log(high) - log_price - drift) / vol
            m["model_prob"] = norm.cdf(z)
        elif high >= 100000:
            #greater than X
            z = (math.log(low) - log_price - drift) / vol
            m["model_prob"] = 1.0 - norm.cdf(z)
        else:
            z_low = (math.log(low) - log_price - drift) / vol
            z_high = (math.log(high) - log_price - drift) / vol
            m["model_prob"] = norm.cdf(z_high) - norm.cdf(z_low)

    return markets


def find_best_bets(markets):
    """find ranges where our model says the market underprices the probability.
    edge = model_prob - market_price (positive = underpriced = good bet)
    also check for pure arb (top-k sum < 1) among highest-probability ranges.
    """
    for m in markets:
        m["edge"] = m["model_prob"] - m["market_price"]
        m["kelly_fraction"] = max(0, (m["model_prob"] * (1 / m["market_price"] - 1) - (1 - m["model_prob"])) / (1 / m["market_price"] - 1)) if m["market_price"] > 0 else 0

    #sort by edge descending
    by_edge = sorted(markets, key=lambda x: x["edge"], reverse=True)

    #also check arb among top market-priced ranges
    by_price = sorted(markets, key=lambda x: x["market_price"], reverse=True)
    for k in range(2, min(5, len(by_price) + 1)):
        top_k = by_price[:k]
        price_sum = sum(m["market_price"] for m in top_k)
        if price_sum < 1.0:
            return {
                "type": "arbitrage",
                "k": k,
                "price_sum": price_sum,
                "profit_margin": 1.0 - price_sum,
                "ranges": top_k,
            }

    #no pure arb, return best edge bets
    good_bets = [m for m in by_edge if m["edge"] > 0.02]
    return {
        "type": "edge",
        "ranges": good_bets if good_bets else by_edge[:3],
    }


def analyze_event(event, current_price, mu, sigma):
    """full analysis pipeline for one btc daily event"""
    markets = parse_event_markets(event)
    if not markets:
        return None

    end_str = event.get("endDate", "")
    try:
        end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        days_ahead = max(0.5, (end_date - datetime.now(timezone.utc)).total_seconds() / 86400)
    except (ValueError, AttributeError):
        days_ahead = 1

    markets = model_range_probabilities(current_price, mu, sigma, markets, days_ahead)
    best = find_best_bets(markets)

    return {
        "event_id": event.get("id"),
        "title": event.get("title"),
        "end_date": end_str,
        "current_btc": round(current_price, 0),
        "days_ahead": round(days_ahead, 1),
        "volatility": round(sigma, 4),
        "best": best,
        "all_ranges": [
            {
                "range": f"${m['low']:,}-${m['high']:,}" if m["high"] < 100000 else f">${m['low']:,}" if m["low"] > 0 else f"<${m['high']:,}",
                "market": round(m["market_price"], 4),
                "model": round(m["model_prob"], 4),
                "edge": round(m["edge"], 4),
            }
            for m in sorted(markets, key=lambda x: x["market_price"], reverse=True)
        ],
    }


def main():
    print("fetching btc volatility...", file=sys.stderr)
    current_price, mu, sigma = get_btc_volatility()
    print(f"BTC: ${current_price:,.0f}, daily vol: {sigma:.4f}", file=sys.stderr)

    print("fetching btc daily events...", file=sys.stderr)
    events = fetch_btc_events()
    print(f"found {len(events)} events", file=sys.stderr)

    results = []
    for event in events:
        analysis = analyze_event(event, current_price, mu, sigma)
        if analysis:
            results.append(analysis)

    with open("btc_ranges.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"wrote {len(results)} analyses to btc_ranges.json")


if __name__ == "__main__":
    main()
