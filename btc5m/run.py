import sys
import time
from btc5m.config import SKIP_BPS, CALM_BPS, DRY_RUN
from btc5m.price import start_price_feed, is_connected
from btc5m.bot import run_window
from btc5m import paper


def main():
    start_price_feed()
    print("waiting for price feed...")
    for _ in range(10):
        if is_connected():
            break
        time.sleep(1)
    if not is_connected():
        print("could not connect to price feed")
        return

    if DRY_RUN:
        print("paper trading mode -- logging to paper_trades.txt")

    skipping = False
    once = "--once" in sys.argv
    windows = 0

    while True:
        swing_bps = run_window(skipping)
        windows += 1

        if swing_bps > SKIP_BPS:
            skipping = True
            print(f"volatile window ({swing_bps:.1f} bps) -- skipping until calm")
        elif skipping and swing_bps < CALM_BPS:
            skipping = False
            print(f"calm window ({swing_bps:.1f} bps) -- resuming trading")
        elif skipping:
            print(f"still volatile ({swing_bps:.1f} bps) -- keep skipping")

        #print summary every 12 windows (1 hour)
        if windows % 12 == 0:
            paper.summary()

        if once:
            paper.summary()
            break


if __name__ == "__main__":
    main()
