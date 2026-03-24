import os
from datetime import datetime

LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "paper_trades.txt")

_total_trades = 0
_total_wins = 0
_total_pnl = 0.0
_total_wagered = 0.0


def _header():
    """write header if file is new"""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write(f"{'time':<20} {'window':<12} {'side':<6} {'entry':>6} {'size':>6} {'cost':>7} {'result':<10} {'pnl':>8} {'total_pnl':>10} {'win_rate':>9} {'btc_open':>12} {'btc_close':>12} {'swing_bps':>10}\n")
            f.write("-" * 140 + "\n")


def log_trade(window_ts, side, entry_price, size, cost, result, pnl, btc_open, btc_close, swing_bps):
    """log a paper trade to file"""
    global _total_trades, _total_wins, _total_pnl, _total_wagered
    _header()

    _total_trades += 1
    _total_pnl += pnl
    _total_wagered += cost
    if pnl > 0:
        _total_wins += 1

    win_rate = _total_wins / _total_trades * 100 if _total_trades > 0 else 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    line = f"{now:<20} {window_ts:<12} {side:<6} {entry_price:>6.3f} {size:>6.1f} {cost:>7.2f} {result:<10} {pnl:>+8.2f} {_total_pnl:>+10.2f} {win_rate:>8.1f}% {btc_open:>12,.2f} {btc_close:>12,.2f} {swing_bps:>10.1f}\n"

    with open(LOG_FILE, "a") as f:
        f.write(line)

    print(f"  paper: {result} pnl={pnl:+.2f} total={_total_pnl:+.2f} ({_total_wins}/{_total_trades} wins)")


def log_skip(window_ts, reason, btc_open, btc_close, swing_bps):
    """log a skipped window"""
    _header()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{now:<20} {window_ts:<12} {'--':<6} {'--':>6} {'--':>6} {'--':>7} {reason:<10} {'--':>8} {_total_pnl:>+10.2f} {'--':>9} {btc_open:>12,.2f} {btc_close:>12,.2f} {swing_bps:>10.1f}\n"

    with open(LOG_FILE, "a") as f:
        f.write(line)


def log_arb(window_ts, up_price, down_price, budget, btc_open, btc_close, swing_bps):
    """log an arb opportunity"""
    global _total_trades, _total_wins, _total_pnl, _total_wagered
    _header()

    total_price = up_price + down_price
    cost = budget
    pnl = round(budget / total_price - budget, 2)

    _total_trades += 1
    _total_wins += 1
    _total_pnl += pnl
    _total_wagered += cost

    win_rate = _total_wins / _total_trades * 100
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{now:<20} {window_ts:<12} {'arb':<6} {total_price:>6.3f} {'--':>6} {cost:>7.2f} {'ARB WIN':<10} {pnl:>+8.2f} {_total_pnl:>+10.2f} {win_rate:>8.1f}% {btc_open:>12,.2f} {btc_close:>12,.2f} {swing_bps:>10.1f}\n"

    with open(LOG_FILE, "a") as f:
        f.write(line)

    print(f"  paper: ARB pnl={pnl:+.2f} total={_total_pnl:+.2f}")


def summary():
    """print running summary"""
    roi = _total_pnl / _total_wagered * 100 if _total_wagered > 0 else 0
    wr = _total_wins / _total_trades * 100 if _total_trades > 0 else 0
    print(f"\n  === paper summary: {_total_trades} trades, {_total_wins} wins ({wr:.0f}%), pnl={_total_pnl:+.2f}, roi={roi:.1f}% ===")
