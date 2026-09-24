"""Analytics — расчёты над данными Finam на лету.

Собрано из наработок Arena/MOEX: индикаторы, дельта/order-flow, стены,
айсберги,(volume profile, SMC-уровни). Один объект = поток данных на выходе.

    fc = FinamConnector(token=...)
    an = Analytics(fc)
    an.load_bars("SBER@MISX", "H1")          # подгрузить бары
    an.indicators()                            # RSI/MACD/BB/EMA/ATR
    an.volume_profile("SBER@MISX", "D1")       # POC/VA
    an.walls_snapshot("SBER@MISX")             # текущие стены + айсберги
"""
import sys, time, threading
from collections import deque
from statistics import mean, stdev

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import Indicators
from walls_v2 import SmartWallTracker


class Analytics:
    def __init__(self, fc, tick: float = 0.01, lot: int = 1):
        self.fc = fc
        self.tick = tick
        self.lot = lot
        self._bars = {}       # symbol+tf -> list[dict]
        self._wall_tracker = SmartWallTracker()
        self._tape = deque(maxlen=2000)
        self._ob = None
        # live-стримы
        self._live = False
        self._lock = threading.Lock()

    # ---------- bars & indicators ----------
    def load_bars(self, symbol: str, timeframe: str = "D1",
                  from_date: str = None, to_date: str = None):
        self._bars[f"{symbol}:{timeframe}"] = self.fc.bars(symbol, timeframe, from_date, to_date)
        return self

    def _get_bars(self, symbol, timeframe):
        key = f"{symbol}:{timeframe}"
        if key not in self._bars:
            self.load_bars(symbol, timeframe)
        return self._bars[key]

    def indicators(self, symbol: str, timeframe: str = "D1") -> dict:
        bars = self._get_bars(symbol, timeframe)
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        if len(closes) < 30:
            return {"error": f"мало баров: {len(closes)}"}
        ind = Indicators([{"c": b["close"], "h": b["high"], "l": b["low"],
                           "o": b["open"], "v": b["volume"], "ts": b.get("ts", 0)}
                          for b in bars])
        rsi_series = ind.rsi(14)
        macd_series = ind.macd()
        atr_series = ind.atr(14)
        rsi_last = rsi_series[-1] if isinstance(rsi_series, list) else rsi_series
        atr_last = atr_series[-1] if isinstance(atr_series, list) else atr_series
        last = closes[-1]
        return {
            "symbol": symbol, "tf": timeframe, "close": last,
            "rsi14": round(rsi_last, 1) if isinstance(rsi_last, (int, float)) else rsi_last,
            "macd": macd_series if not isinstance(macd_series, dict) else
                    {k: round(v, 4) for k, v in macd_series.items()},
            "ema20_50_cross": (lambda r: r[-1] if isinstance(r, list) and r else r)(
                ind.ema_cross(20, 50)) if hasattr(ind, "ema_cross") else
                (lambda r: r[-1] if isinstance(r, list) and r else r)(
                ind.trend(20, 50)) if hasattr(ind, "trend") else "?",
            "bb": ind.bollinger(),
            "atr14": round(atr_last, 2) if isinstance(atr_last, (int, float)) else atr_last,
            "trend": ind.trend() if hasattr(ind, "trend") else None,
        }

    def volume_profile(self, symbol: str, timeframe: str = "D1", bins: int = 30) -> dict:
        """POC, Value Area 70%, профиль объёма по барам."""
        bars = self._get_bars(symbol, timeframe)
        if not bars:
            return {"error": "нет баров"}
        lo = min(b["low"] for b in bars)
        hi = max(b["high"] for b in bars)
        if hi <= lo:
            return {"error": "вырожденный диапазон"}
        step = (hi - lo) / bins
        profile = [0.0] * bins
        for b in bars:
            c = b["close"]
            idx = min(bins - 1, int((c - lo) / step))
            profile[idx] += b["volume"]
        total = sum(profile)
        poc_idx = profile.index(max(profile))
        # value area: расширяемся от POC до 70%
        va = {poc_idx}
        vol = profile[poc_idx]
        lo_i = hi_i = poc_idx
        while vol < total * 0.7 and (lo_i > 0 or hi_i < bins - 1):
            up = profile[hi_i + 1] if hi_i < bins - 1 else -1
            dn = profile[lo_i - 1] if lo_i > 0 else -1
            if up >= dn:
                hi_i += 1; vol += up
            else:
                lo_i -= 1; vol += dn
        return {"symbol": symbol, "poc": round(lo + (poc_idx + 0.5) * step, 2),
                "va_high": round(lo + (hi_i + 1) * step, 2),
                "va_low": round(lo + lo_i * step, 2),
                "profile": [{"price": round(lo + (i + .5) * step, 2), "vol": v} for i, v in enumerate(profile)]}

    # ---------- live: лента + стакан -> стены/айсберги/поток ----------
    def start_live(self, symbol: str, tape_iv: float = 0.0, ob_iv: float = 0.0):
        """Стриминг-режим: тики + стакан в трекер. tape_iv/ob_iv: min интервал
        обработки (0 = каждый апдейт)."""
        self.symbol = symbol

        def on_trade(t):
            with self._lock:
                self._tape.append({"side": t["side"], "price": t["price"],
                                   "size": t["size"], "ts": time.time()})

        def on_ob(ob):
            if ob_iv and hasattr(self, "_last_ob") and time.time() - self._last_ob < ob_iv:
                return
            self._last_ob = time.time()
            with self._lock:
                self._ob = ob
                mid = (ob["bids"][0][0] + ob["asks"][0][0]) / 2 if ob["bids"] and ob["asks"] else None
                if mid:
                    self._wall_tracker.update(
                        {"mid": mid, "spread": ob["asks"][0][0] - ob["bids"][0][0],
                         "imbalance": 0, "bids": ob["bids"], "asks": ob["asks"],
                         "tape": list(self._tape)[-10:]}, list(self._tape)[-10:])

        self.fc.stream_trades(symbol, on_trade)
        self.fc.stream_orderbook(symbol, on_ob)
        self._live = True

    def walls_snapshot(self) -> dict:
        """Текущие стены + айсберги + сигналы (требует start_live)."""
        rep = self._wall_tracker.last_report or {}
        walls = []
        for side in ("bid", "ask"):
            for p, w in self._wall_tracker.walls[side].items():
                walls.append({"side": side, "price": p, "size": w["size"],
                              "size0": w["size0"], "hits": w["hits"],
                              "age": w["age"], "iceberg": w["iceberg"],
                              "eaten_pct": round(w["eaten_lots"] / max(1, w["size0"]), 2)})
        return {"walls": sorted(walls, key=lambda x: -x["size"])[:10],
                "signals": rep.get("signals", []), "features": self._wall_tracker.features}

    def flow_snapshot(self) -> dict:
        """CVD/поток из live-ленты."""
        with self._lock:
            buys = sum(t["size"] for t in self._tape if t["side"] == "buy")
            sells = sum(t["size"] for t in self._tape if t["side"] == "sell")
            return {"cvd": buys - sells, "buy_vol": buys, "sell_vol": sells,
                    "n_trades": len(self._tape),
                    "flow_imbalance": (buys - sells) / (buys + sells) if buys + sells else 0}

    # ---------- scoring: сводные данные для гейта ----------
    def gate_state(self, symbol: str, timeframe: str = "H1") -> dict:
        """Полный state для Laya/Jev-гейта: индикаторы + поток + стены."""
        ind = self.indicators(symbol, timeframe)
        flow = self.flow_snapshot()
        walls = self.walls_snapshot()
        return {"indicators": ind, "flow": flow,
                "walls": walls.get("walls", [])[:5],
                "signals": walls.get("signals", [])[-3:]}
