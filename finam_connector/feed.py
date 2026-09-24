"""Market data: order book + tape via FinamPy gRPC."""
import sys, time, threading
from collections import deque

sys.path.insert(0, "/home/user/FinamPy")
from FinamPy import FinamPy
from FinamPy.grpc.marketdata_service_pb2 import QuoteRequest, OrderBookRequest, LatestTradesRequest

from config import FINAM_TOKEN, SYMBOL, LOOP_SEC


def _f(d) -> float:
    try:
        return float(d.value) if d.value else 0.0
    except Exception:
        return 0.0


class MarketFeed:
    """Один поток: раз в LOOP_SEC тянет стакан + последнюю ленту сделок.
    Держит: книгу (bids/asks списки [(price, size)]), ленту (deque), mid/spread."""

    def __init__(self, symbol: str = SYMBOL, tape_len: int = 500):
        self.fp = FinamPy(FINAM_TOKEN)
        self.symbol = symbol
        self.loop = LOOP_SEC
        self.book = {"bids": [], "asks": []}   # sorted: bids desc, asks asc
        self.mid = None
        self.spread = None
        self.tape = deque(maxlen=tape_len)     # dicts {ts, price, size, side}
        self.last_trade_id = 0
        self.imbalance = 0.0
        self._lock = threading.Lock()
        self.running = False
        self.errors = 0
        self.updates = 0

    def start(self):
        self.running = True
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self.running = False

    def _run(self):
        while self.running:
            try:
                self._poll()
                self.errors = 0
            except Exception as e:
                self.errors += 1
                if self.errors <= 3 or self.errors % 20 == 0:
                    print(f"[feed] error #{self.errors}: {e}")
                time.sleep(min(30, 2 ** min(self.errors, 4)))
            time.sleep(self.loop)

    def _poll(self):
        ob = self.fp.call_function(self.fp.marketdata_stub.OrderBook,
                                   OrderBookRequest(symbol=self.symbol))
        bids, asks = [], []
        for row in ob.orderbook.rows:
            p, b, s = _f(row.price), _f(row.buy_size), _f(row.sell_size)
            if b > 0:
                bids.append((p, b))
            if s > 0:
                asks.append((p, s))
        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])
        with self._lock:
            self.book = {"bids": bids, "asks": asks}
            if bids and asks:
                self.mid = (bids[0][0] + asks[0][0]) / 2
                self.spread = asks[0][0] - bids[0][0]
                tot_b = sum(s for _, s in bids[:5])
                tot_a = sum(s for _, s in asks[:5])
                self.imbalance = (tot_b - tot_a) / (tot_b + tot_a) if (tot_b + tot_a) else 0.0
            self.updates += 1
        self._poll_trades()

    def _poll_trades(self):
        tr = self.fp.call_function(self.fp.marketdata_stub.LatestTrades,
                                   LatestTradesRequest(symbol=self.symbol))
        new = []
        for t in reversed(list(tr.trades)):
            tid = int(getattr(t, "trade_id", 0) or 0)
            if tid and tid <= self.last_trade_id:
                continue
            side = "buy" if t.side == 1 else ("sell" if t.side == 2 else None)
            size = _f(t.size) or _f(getattr(t, "quantity", None))
            new.append({"ts": t.timestamp.seconds, "id": tid,
                        "price": _f(t.price), "size": size, "side": side})
        if new:
            with self._lock:
                for n in new:
                    self.tape.append(n)
                ids = [n["id"] for n in new if n["id"]]
                if ids:
                    self.last_trade_id = max(ids)

    def snapshot(self) -> dict:
        """Атомарный срез для модели."""
        with self._lock:
            return {
                "mid": self.mid,
                "spread": self.spread,
                "imbalance": self.imbalance,
                "bids": list(self.book["bids"][:10]),
                "asks": list(self.book["asks"][:10]),
                "tape": list(self.tape)[-100:],
            }

    def close(self):
        self.stop()
        try:
            self.fp.close_channel()
        except Exception:
            pass


if __name__ == "__main__":
    import time as _t
    mf = MarketFeed()
    mf.start()
    for i in range(3):
        _t.sleep(2.5)
        s = mf.snapshot()
        print(f"upd={mf.updates} mid={s['mid']} spread={s['spread']} imb={s['imbalance']:+.2f} "
              f"bids={len(s['bids'])} asks={len(s['asks'])} tape={len(s['tape'])}")
    mf.close()
