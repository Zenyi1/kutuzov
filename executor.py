from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from config import (
    POLYMARKET_API_KEY,
    POLYMARKET_SECRET,
    POLYMARKET_PASSPHRASE,
    PRIVATE_KEY,
    CLOB_API_URL,
    DRY_RUN,
    BET_BUDGET,
)
from analyzer import compute_bet_amounts
from notifier import notify_execution


def create_client():
    """initialize the polymarket clob client"""
    client = ClobClient(
        CLOB_API_URL,
        key=POLYMARKET_API_KEY,
        chain_id=137,  #polygon
        funder=PRIVATE_KEY,
    )

    #set api creds for authenticated endpoints
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def execute_opportunity(opportunity, budget=None):
    """place bets on the top k outcomes of an opportunity"""
    budget = budget or BET_BUDGET
    bets = compute_bet_amounts(opportunity, budget)

    if DRY_RUN:
        print("[executor] DRY_RUN enabled, not placing real orders")
        print(f"[executor] would place {len(bets)} orders:")
        for b in bets:
            print(f"  buy {b['expected_shares']:.2f} YES shares of '{b['question']}' @ {b['price']:.4f} for ${b['amount']:.2f}")
        return bets

    client = create_client()
    placed = []

    for bet in bets:
        token_id = bet["yes_token_id"]
        if not token_id:
            print(f"[executor] skipping '{bet['question']}' - no token id")
            continue

        try:
            order_args = OrderArgs(
                price=bet["price"],
                size=bet["expected_shares"],
                side="BUY",
                token_id=token_id,
            )

            signed_order = client.create_order(order_args)
            result = client.post_order(signed_order, OrderType.GTC)
            print(f"[executor] order placed: {bet['question']} -> {result}")

            bet["order_result"] = result
            placed.append(bet)

        except Exception as e:
            print(f"[executor] failed to place order for '{bet['question']}': {e}")

    total_spent = sum(b["amount"] for b in placed)
    notify_execution(opportunity["event_title"], placed, total_spent)
    return placed
