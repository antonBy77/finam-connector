"""FinamConnector — единый клиент Finam Trade API.

REST (Arena-style) + gRPC (FinamPy) под одним интерфейсом:
- REST быстрый для баров/котировок, gRPC для стриминга и стакана
- встроенный rate limiter (200 req/min)
- JWT auto-renew через SubscribeJwtRenewal (опция)

Использование:
    from finam_connector import FinamConnector
    fc = FinamConnector(token="...")
    fc.quote("SBER@MISX")
    fc.bars("SBER@MISX", "D1", "01.09.2026", "24.09.2026")
    fc.stream_trades("SBER@MISX", callback=print)   # генератор/колбэк
    fc.stream_orderbook("SBER@MISX", callback=print)
"""
import sys, time, threading, json
from collections import deque

# FinamPy лежит рядом или в /home/user/FinamPy
_FINAMPY_PATHS = ["/home/user/FinamPy", "/opt/FinamPy"]


def _ensure_finampy():
    for p in _FINAMPY_PATHS:
        try:
            sys.path.insert(0, p)
            import FinamPy  # noqa
            return True
        except ImportError:
            continue
    return False


class RateLimiter:
    """Token bucket: max N запросов в минуту."""

    def __init__(self, per_minute: int = 190):
        self.per_minute = per_minute
        self.window = deque()

    def wait(self):
        now = time.time()
        while self.window and now - self.window[0] > 60:
            self.window.popleft()
        if len(self.window) >= self.per_minute:
            sleep_for = 60 - (now - self.window[0]) + 0.05
            time.sleep(max(0, sleep_for))
        self.window.append(time.time())


class FinamConnector:
    def __init__(self, token: str, account_id: str = "", rest_base: str = "https://api.finam.ru/v1",
                 rate_per_minute: int = 190, use_grpc: bool = True):
        self.token = token
        self.account_id = account_id
        self.rest_base = rest_base
        self.limiter = RateLimiter(rate_per_minute)
        self._fp = None
        self._streams = []   # активные стрим-треды (daemon)
        if use_grpc and _ensure_finampy():
            try:
                from FinamPy import FinamPy
                self._fp = FinamPy(token)
            except Exception as e:
                print(f"[FinamConnector] gRPC init failed: {e}; REST-only mode")

    # ---------- REST (arena_data.py style) ----------
    def _rest(self, method: str, path: str, body: dict = None) -> dict:
        import urllib.request
        url = f"{self.rest_base}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        self.limiter.wait()
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    # ---------- Market data: unary ----------
    def quote(self, symbol: str) -> dict:
        """Последняя котировка: bid/ask/last/ohlc/volume."""
        if self._fp:
            from FinamPy.grpc.marketdata_service_pb2 import QuoteRequest
            q = self._fp.call_function(self._fp.marketdata_stub.LastQuote,
                                       QuoteRequest(symbol=symbol)).quote
            f = lambda d: float(d.value) if d.value else 0.0
            return {"symbol": symbol, "bid": f(q.bid), "ask": f(q.ask),
                    "last": f(q.last), "open": f(q.open), "high": f(q.high),
                    "low": f(q.low), "close": f(q.close), "volume": f(q.volume)}
        return self._rest("GET", f"/instruments/quote?symbol={symbol}")

    def orderbook(self, symbol: str, depth: int = 0) -> dict:
        """Стакан. depth=0 — все уровни, иначе первые N.
        Формат: {"bids": [(price,size)], "asks": [(price,size)]} (bids desc)."""
        from FinamPy.grpc.marketdata_service_pb2 import OrderBookRequest
        ob = self._fp.call_function(self._fp.marketdata_stub.OrderBook,
                                    OrderBookRequest(symbol=symbol))
        f = lambda d: float(d.value) if d.value else 0.0
        bids, asks = [], []
        for row in ob.orderbook.rows:
            p, b, s = f(row.price), f(row.buy_size), f(row.sell_size)
            if b > 0:
                bids.append((p, b))
            if s > 0:
                asks.append((p, s))
        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])
        if depth:
            bids, asks = bids[:depth], asks[:depth]
        return {"symbol": symbol, "bids": bids, "asks": asks}

    def latest_trades(self, symbol: str, limit: int = 100) -> list:
        """Последние сделки с side: [{"price","size","side","ts","id"}]."""
        from FinamPy.grpc.marketdata_service_pb2 import LatestTradesRequest
        tr = self._fp.call_function(self._fp.marketdata_stub.LatestTrades,
                                    LatestTradesRequest(symbol=symbol))
        out = []
        for t in tr.trades:
            out.append({"id": int(t.trade_id or 0),
                        "price": float(t.price.value) if t.price.value else 0.0,
                        "size": float(t.size.value) if t.size.value else 0.0,
                        "side": "buy" if t.side == 1 else "sell",
                        "ts": t.timestamp.seconds})
        return out[-limit:]

    def bars(self, symbol: str, timeframe: str = "D1",
             from_date: str = None, to_date: str = None, count: int = 150) -> list:
        """Бары OHLCV. timeframe: M1,M5,M15,M30,H1,H4,D1,W1,MN1.
        from/to: 'DD.MM.YYYY'. Возвращает list[dict] старые->новые."""
        TF = {"M1": 1, "M5": 5, "M15": 9, "M30": 11, "H1": 12, "H4": 15,
              "D1": 19, "W1": 20, "MN1": 21}
        from FinamPy.grpc.marketdata_service_pb2 import BarsRequest, TimeFrame
        from google.type.interval_pb2 import Interval
        from google.protobuf.timestamp_pb2 import Timestamp
        from datetime import datetime, timedelta

        def pd(s):
            return datetime.strptime(s, "%d.%m.%Y")
        end = pd(to_date) if to_date else datetime.now()
        start = pd(from_date) if from_date else end - timedelta(days=60)
        req = BarsRequest(symbol=symbol,
                          timeframe=getattr(TimeFrame, f"TIME_FRAME_{timeframe.replace('MN1','MN').replace('MN','M')}") if False else TF_MAP[timeframe],
                          interval=Interval(start_time=Timestamp(seconds=int(start.timestamp())),
                                            end_time=Timestamp(seconds=int(end.timestamp()))))
        res = self._fp.call_function(self._fp.marketdata_stub.Bars, req)
        f = lambda d: float(d.value) if d.value else 0.0
        out = []
        for b in res.bars:
            out.append({"ts": b.timestamp.seconds, "open": f(b.open), "high": f(b.high),
                        "low": f(b.low), "close": f(b.close), "volume": f(b.volume)})
        return out

    # ---------- Streaming ----------
    def stream_trades(self, symbol: str, callback, reconnect: bool = True):
        """Подписка на сделки: callback(dict) на каждый тик.
        dict: {"price","size","side","ts"}. Тред-daemon."""
        if not self._fp:
            raise RuntimeError("gRPC недоступен")
        from FinamPy.grpc.marketdata_service_pb2 import SubscribeLatestTradesRequest

        def _run():
            while True:
                try:
                    stream = self._fp.marketdata_stub.SubscribeLatestTrades(
                        request=SubscribeLatestTradesRequest(symbol=symbol),
                        metadata=(self._fp.metadata,))
                    for ev in stream:
                        for t in ev.trades:
                            callback({"symbol": symbol,
                                      "price": float(t.price.value) if t.price.value else 0.0,
                                      "size": float(t.size.value) if t.size.value else 0.0,
                                      "side": "buy" if t.side == 1 else "sell",
                                      "ts": t.timestamp.seconds})
                except Exception as e:
                    print(f"[stream_trades {symbol}] {type(e).__name__}: {str(e)[:80]}; reconnect 3s")
                    if not reconnect:
                        return
                    time.sleep(3)

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        self._streams.append(th)
        return th

    def stream_orderbook(self, symbol: str, callback, reconnect: bool = True):
        """Подписка на стакан: callback({"bids","asks"}) на каждый апдейт.
        Ответ ev.order_book — список групп, у каждой .rows (price/buy_size/sell_size)."""
        if not self._fp:
            raise RuntimeError("gRPC недоступен")
        from FinamPy.grpc.marketdata_service_pb2 import SubscribeOrderBookRequest

        def _parse(ev):
            f = lambda d: float(d.value) if getattr(d, "value", None) else 0.0
            bids, asks = [], []
            # схема A: ev.order_book = [группы с .rows]
            groups = getattr(ev, "order_book", None)
            if groups is not None and len(groups):
                for g in groups:
                    for row in getattr(g, "rows", []):
                        p, b, s = f(row.price), f(row.buy_size), f(row.sell_size)
                        if b > 0:
                            bids.append((p, b))
                        if s > 0:
                            asks.append((p, s))
            else:
                # схема B: ev.orderbook.rows (unary-подобная)
                for row in getattr(getattr(ev, "orderbook", None), "rows", []) or []:
                    p, b, s = f(row.price), f(row.buy_size), f(row.sell_size)
                    if b > 0:
                        bids.append((p, b))
                    if s > 0:
                        asks.append((p, s))
            bids.sort(key=lambda x: -x[0])
            asks.sort(key=lambda x: x[0])
            return {"symbol": symbol, "bids": bids, "asks": asks}

        def _run():
            while True:
                try:
                    stream = self._fp.marketdata_stub.SubscribeOrderBook(
                        request=SubscribeOrderBookRequest(symbol=symbol),
                        metadata=(self._fp.metadata,))
                    for ev in stream:
                        callback(_parse(ev))
                except Exception as e:
                    print(f"[stream_ob {symbol}] {type(e).__name__}: {str(e)[:80]}; reconnect 3s")
                    if not reconnect:
                        return
                    time.sleep(3)

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        self._streams.append(th)
        return th

    def close(self):
        if self._fp:
            try:
                self._fp.close_channel()
            except Exception:
                pass

    # ---------- Trading ----------
    def place_order(self, symbol: str, side: str, quantity: int,
                    price: float = None, sl: float = None, tp: float = None) -> dict:
        """Лимитный/рыночный ордер. ТРЕБУЕТ подтверждения Антона отдельно."""
        from FinamPy.grpc.orders_service_pb2 import Order, OrderType
        from FinamPy.grpc.side_pb2 import SIDE_BUY, SIDE_SELL
        from google.type.decimal_pb2 import Decimal
        from datetime import datetime
        o = Order(account_id=self.account_id, symbol=symbol,
                  quantity=Decimal(value=str(quantity)),
                  side=SIDE_BUY if side == "buy" else SIDE_SELL,
                  type=OrderType.ORDER_TYPE_LIMIT if price else OrderType.ORDER_TYPE_MARKET,
                  client_order_id=str(int(datetime.now().timestamp() * 1000)))
        if price:
            o.price.CopyFrom(Decimal(value=str(price)))
        st = self._fp.call_function(self._fp.orders_stub.PlaceOrder, o)
        return {"order_id": st.order_id, "state": str(st.state)}

    def cancel_order(self, order_id) -> dict:
        from FinamPy.grpc.orders_service_pb2 import CancelOrderRequest
        return self._fp.call_function(self._fp.orders_stub.CancelOrder,
                                      CancelOrderRequest(account_id=self.account_id, order_id=order_id))


# bars() TF map (module-level, чтобы не пересоздавать)
TF_MAP = {}


def _init_tf_map():
    from FinamPy.grpc.marketdata_service_pb2 import TimeFrame
    return {"M1": TimeFrame.TIME_FRAME_M1, "M5": TimeFrame.TIME_FRAME_M5,
            "M15": TimeFrame.TIME_FRAME_M15, "M30": TimeFrame.TIME_FRAME_M30,
            "H1": TimeFrame.TIME_FRAME_H1, "H4": TimeFrame.TIME_FRAME_H4,
            "D1": TimeFrame.TIME_FRAME_D, "W1": TimeFrame.TIME_FRAME_W1,
            "MN1": TimeFrame.TIME_FRAME_MN}


_init_tf_map_original = None


def bars_fixed(self, symbol, timeframe="D1", from_date=None, to_date=None):
    from FinamPy.grpc.marketdata_service_pb2 import BarsRequest, TimeFrame
    from google.type.interval_pb2 import Interval
    from google.protobuf.timestamp_pb2 import Timestamp
    from datetime import datetime, timedelta
    tfmap = {"M1": TimeFrame.TIME_FRAME_M1, "M5": TimeFrame.TIME_FRAME_M5,
             "M15": TimeFrame.TIME_FRAME_M15, "M30": TimeFrame.TIME_FRAME_M30,
             "H1": TimeFrame.TIME_FRAME_H1, "H2": TimeFrame.TIME_FRAME_H2,
             "H4": TimeFrame.TIME_FRAME_H4, "H8": TimeFrame.TIME_FRAME_H8,
             "D1": TimeFrame.TIME_FRAME_D, "W1": TimeFrame.TIME_FRAME_W,
             "MN1": TimeFrame.TIME_FRAME_MN}
    end = datetime.strptime(to_date, "%d.%m.%Y") if to_date else datetime.now()
    start = datetime.strptime(from_date, "%d.%m.%Y") if from_date else end - timedelta(days=60)
    req = BarsRequest(symbol=symbol, timeframe=tfmap[timeframe],
                      interval=Interval(start_time=Timestamp(seconds=int(start.timestamp())),
                                        end_time=Timestamp(seconds=int(end.timestamp()))))
    res = self._fp.call_function(self._fp.marketdata_stub.Bars, req)
    if res is None or not getattr(res, "bars", None):
        return []
    f = lambda d: float(d.value) if d.value else 0.0
    return [{"ts": b.timestamp.seconds, "open": f(b.open), "high": f(b.high),
             "low": f(b.low), "close": f(b.close), "volume": f(b.volume)}
            for b in res.bars]


FinamConnector.bars = bars_fixed
