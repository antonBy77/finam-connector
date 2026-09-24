"""Wall detection & tracking + wall-based trading signals.

Стена = уровень в топ-10 стакана с объёмом >= K * средний по топ-10 той же стороны.

Жизненный цикл (по обновлениям стакана ~2с):
- appear: стена появилась
- hold:   цена коснулась/била в стену, стена на месте -> отбой (rejection)
- eaten:  в стену били (принты на её уровне) и объём упал -> пробой
- pulled: объём исчез, но в неё НЕ били -> сняли (спуфинг/перестановка) -> сигнал против прежней стороны

Сигналы:
  BUY  rejection: цена тестировала bid-стену сверху, sell-принты в неё, стена держится
  SELL rejection: зеркально от ask-стены
  BUY  breakout:  ask-стену СЪЕЛИ (объём упал при buy-принтах) -> импульс вверх
  SELL breakout:  bid-стену съели -> импульс вниз
  SELL pull:      bid-стену сняли без боевых принтов
  BUY  pull:      ask-стену сняли без боевых принтов
"""
from collections import defaultdict

from config import TICK


class WallTracker:
    def __init__(self, k: float = 3.0, min_size: float = 3000,
                 touch_bps: float = 3.0, hold_min_updates: int = 2,
                 pull_stay_bps: float = 2.0):
        self.k = k                 # во сколько раз больше среднего
        self.min_size = min_size   # минимум лотов для статуса стены
        self.touch_bps = touch_bps # насколько близко цена считается "тестом" стены
        self.hold_min = hold_min_updates
        self.pull_stay_bps = pull_stay_bps  # max отход mid от стены для валидного pull
        self.walls = {"bid": defaultdict(dict), "ask": defaultdict(dict)}
        self.last_mid = None
        self.last_report = None

    def _avg_top(self, levels) -> float:
        return sum(s for _, s in levels) / len(levels) if levels else 0.0

    def update(self, snap: dict, tape: list) -> dict:
        """Вызывается раз в цикл. Возвращает отчёт + сигнал."""
        bids, asks = snap["bids"], snap["asks"]  # [(price, size)] top-10
        mid = snap["mid"]
        new_trades = tape[-50:]  # принты за окно (до 50)

        report = {"walls": [], "signals": []}
        if not bids or not asks:
            return report

        for side, levels in (("bid", bids), ("ask", asks)):
            avg = self._avg_top(levels)
            cur = {}
            for price, size in levels:
                others = [s for p, s in levels if p != price]
                ref = sum(others) / len(others) if others else 0.0
                if ref and size >= max(self.k * ref, self.min_size):
                    cur[price] = size
            store = self.walls[side]
            # обновить существующие / создать новые
            for price, size in cur.items():
                w = store.get(price)
                hit = sum(t["size"] for t in new_trades
                          if t["side"] == ("sell" if side == "bid" else "buy")
                          and abs(t["price"] - price) <= TICK * 1.01)
                if w:
                    w["age"] += 1
                    drop = w["size"] - size
                    w["size"] = size
                    if drop > 0:
                        w["hit_lots"] += hit
                        # любой сброс объёма при наличии боевых принтов = съедание
                        if w["hit_lots"] > 0:
                            w["eaten_lots"] += drop
                            w["eaten_pending"] = w["eaten_lots"]
                else:
                    store[price] = {"size0": size, "size": size, "age": 1,
                                    "hit_lots": hit, "eaten_lots": 0}
            # исчезнувшие / сильно съеденные
            for price in list(store.keys()):
                w = store[price]
                eaten_ratio = (w.get("eaten_pending", w["eaten_lots"])) / max(1.0, w["size0"])
                if price in cur and eaten_ratio < 0.4:
                    continue  # стена жива и не съедена
                store.pop(price)
                # pull валиден только если цена осталась на месте (спуфинг),
                # а не уехала от стены вместе со шкалой
                if mid and abs(mid - price) / mid * 1e4 > self.pull_stay_bps:
                    continue
                if w["age"] >= self.hold_min:
                    ev = {"side": side, "price": price, "size0": w["size0"],
                          "age": w["age"], "eaten_ratio": round(eaten_ratio, 2)}
                    if eaten_ratio >= 0.4 and w["hit_lots"] > 0:
                        ev["event"] = "eaten"
                        report["signals"].append(self._signal(side, "breakout", ev, mid))
                    else:
                        ev["event"] = "pulled"
                        report["signals"].append(self._signal(side, "pull", ev, mid))
                    report["walls"].append(ev)
            # живые стены: тесты
            for price, w in store.items():
                near = mid and abs(mid - price) / mid * 1e4 <= self.touch_bps * 30  # ~ до 90bps внимание
                if w["age"] >= self.hold_min and w["hit_lots"] > 0 and near:
                    report["walls"].append({"side": side, "price": price, "size": w["size"],
                                            "age": w["age"], "hit_lots": w["hit_lots"],
                                            "event": "hold"})
                    report["signals"].append(self._signal(side, "rejection", {
                        "side": side, "price": price, "size": w["size"],
                        "age": w["age"], "hit_lots": w["hit_lots"]}, mid))
        self.last_mid = mid
        self.last_report = report
        return report

    @staticmethod
    def _signal(wall_side: str, kind: str, ev: dict, mid) -> dict | None:
        if not mid:
            return None
        # bid-стена: rejection->BUY, breakout->SELL, pull->SELL; ask: зеркально
        action_map = {
            ("bid", "rejection"): "buy", ("ask", "rejection"): "sell",
            ("bid", "breakout"): "sell", ("ask", "breakout"): "buy",
            ("bid", "pull"): "sell", ("ask", "pull"): "buy",
        }
        action = action_map[(wall_side, kind)]
        return {
            "action": action, "kind": kind, "wall_side": wall_side,
            "wall_price": ev["price"], "wall_size0": ev.get("size0", ev.get("size")),
            "mid": mid, "reason": f"{wall_side}-wall {kind} @ {ev['price']} "
                                  f"(size0={ev.get('size0', ev.get('size'))}, age={ev['age']})",
        }


class WallModel:
    """Decision interface совместим с trader.py: decide(snap) -> decision dict.
    Вход только по свежему сигналу от стены; иначе hold."""
    name = "wall-v1"

    def __init__(self, tracker: WallTracker = None):
        self.tracker = tracker or WallTracker()
        self.last_signal_block = -1
        self.signal_ttl = 3  # сигнал валиден N циклов

    def decide(self, snap: dict) -> dict:
        tape = snap["tape"]
        report = self.tracker.update(snap, tape)
        sig = report["signals"][-1] if report["signals"] else None
        block = self.tracker_update_count()
        fresh = sig is not None and block - self.last_signal_block <= self.signal_ttl
        if sig is not None:
            self.last_signal_block = block
        score = 0.5 if (sig and sig["action"] == "buy") else (-0.5 if (sig and sig["action"] == "sell") else 0.0)
        return {
            "action": (sig["action"] if sig else "hold"),
            "probabilities": {"buy": max(0, 0.5 + score / 2), "sell": max(0, 0.5 - score / 2), "hold": 0.0},
            "score": score,
            "confidence": abs(score),
            "signal": sig,
            "features": {"n_walls": len(self.tracker.walls["bid"]) + self.tracker.walls["ask"].__len__(),
                         "last_report": self.tracker.last_report is not None},
            "latency_ms": 0.2,
        }

    def _n(self):
        self._c = getattr(self, "_c", 0) + 1
        return self._c

    def tracker_update_count(self):
        return self._n()
