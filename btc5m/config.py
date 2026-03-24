import os
import sys
from dotenv import load_dotenv

#load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"

#trading params
BUDGET = float(os.getenv("BTC5M_BUDGET", "5"))
ENTRY_LOW = float(os.getenv("BTC5M_ENTRY_LOW", "0.20"))
ENTRY_HIGH = float(os.getenv("BTC5M_ENTRY_HIGH", "0.30"))
TP_PRICE = float(os.getenv("BTC5M_TP_PRICE", "0.40"))

#thresholds in basis points
SWING_BPS = int(os.getenv("BTC5M_SWING_BPS", "10"))
SKIP_BPS = int(os.getenv("BTC5M_SKIP_BPS", "11"))
CALM_BPS = int(os.getenv("BTC5M_CALM_BPS", "5"))

#arb
ARB_THRESHOLD = float(os.getenv("BTC5M_ARB_THRESHOLD", "0.95"))

#order book
MIN_BOOK_SIZE = float(os.getenv("BTC5M_MIN_BOOK_SIZE", "10"))

#timing (seconds)
ENTRY_WINDOW = int(os.getenv("BTC5M_ENTRY_WINDOW", "120"))
EXIT_START = int(os.getenv("BTC5M_EXIT_START", "240"))
