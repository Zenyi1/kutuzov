import json
import time
import threading
import requests


_price = 0.0
_lock = threading.Lock()
_history = []
_connected = False

BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"
BINANCE_REST = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"


def _on_message(ws, message):
    global _price, _connected
    data = json.loads(message)
    p = float(data["p"])
    t = float(data["T"]) / 1000
    with _lock:
        _price = p
        _history.append((t, p))
        #keep last 600 entries (~5 min of ticks)
        if len(_history) > 600:
            _history.pop(0)
        _connected = True


def _on_error(ws, error):
    print(f"  ws error: {error}")


def _on_close(ws, close_status, close_msg):
    global _connected
    _connected = False
    print("  ws closed, reconnecting in 5s...")
    time.sleep(5)
    _start_ws()


def _start_ws():
    try:
        from websocket import WebSocketApp
        ws = WebSocketApp(
            BINANCE_WS,
            on_message=_on_message,
            on_error=_on_error,
            on_close=_on_close,
        )
        ws.run_forever()
    except Exception as e:
        print(f"  ws failed: {e}, falling back to REST polling")
        _poll_rest()


def _poll_rest():
    """fallback: poll binance REST api every 1s"""
    global _price, _connected
    while True:
        try:
            r = requests.get(BINANCE_REST, timeout=5)
            p = float(r.json()["price"])
            t = time.time()
            with _lock:
                _price = p
                _history.append((t, p))
                if len(_history) > 600:
                    _history.pop(0)
                _connected = True
        except Exception:
            pass
        time.sleep(1)


def start_price_feed():
    """start background thread for btc price"""
    thread = threading.Thread(target=_start_ws, daemon=True)
    thread.start()


def get_btc_price():
    with _lock:
        return _price


def clear_history():
    """clear price history for new window"""
    with _lock:
        _history.clear()


def price_change_bps(start, now):
    """compute price change in basis points"""
    if start == 0:
        return 0
    return abs(now - start) / start * 10000


def is_connected():
    return _connected
