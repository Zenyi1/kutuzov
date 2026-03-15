## kutuzov — polymarket multi-outcome arbitrage bot

scans polymarket for events where top-k outcome prices sum < 1.0, guaranteeing profit if any wins.

### usage
```
python main.py --scan              # json output of all opportunities
python main.py --execute <id>      # place bets on event (needs .env creds)
python main.py --monitor           # check open positions
python main.py                     # continuous 30min scanner with telegram alerts
```

### how it works
- `discovery.py` fetches active multi-outcome events from gamma api (public, no keys)
- `analyzer.py` finds top-k where sum(prices) < 1.0, allocates proportional to price for equal payouts
- `executor.py` places orders via py-clob-client (needs api keys, defaults DRY_RUN=true)
- `notifier.py` sends telegram alerts
- `monitor.py` tracks positions in positions.json

### bugs solved
- inactive/placeholder markets had `bestAsk=1` — fix: filter `active=True` only
- inverse allocation gave unequal payouts — fix: allocate proportional to price
- discovery prints polluted json stdout — fix: status prints go to stderr

### rules
- keep code minimal, no fix-on-fix
- scan output is json to stdout, status to stderr
- venv at `./venv/`, activate before running
