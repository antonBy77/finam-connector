"""Обогащение данных + умные сигналы v2.

Новое против walls.py v1:
1. Раздельная логика касаний:
   - 1-е касание стены -> SIGNAL REJECTION (отбой) — стена свежая, лимитный
     интерес настоящий
   - стена ПРОЕДАЕТСЯ (объём устойчиво падает) и hit_count >= 3 ->
     SIGNAL BREAKOUT — защищавшийся исчерпал ресурс, оставшийся объём
     часто iceberg
2. Айсберг-детектор: уровень, объём которого ВОССТАНАВЛИВАЕТСЯ после
   съедания (size упал >20%, потом вернулся к >=90% от size0) при
   продолжающихся принтах в него — классический iceberg. Держится
   аномально долго -> трейдер с большим пассивом, сигнал УСИЛИВАЕТ отбой.
3. Признаки для гейта (features dict на каждый сигнал):
   - wall: ratio, size, size0, eaten_pct, hits, age, is_iceberg
   - flow: cvd_1m, tape_intensity (принтов/с), agressor_ratio
   - market: trend_5m (bps), day_range_pos (0..1), vola_5m
   - micro: spread_bps, imbalance_top3, mid_velocity
"""
from collections import defaultdict, deque

TICK = 0.01  # дефолт шаг цены; переопределяется через Analytics(tick=...)


class SmartWallTracker:
    def __init__(self, k: float = 3.0, min_size: float = 3000,
                 hold_min_updates: int = 2, pull_stay_bps: float = 3.0,
                 eat_threshold: float = 0.9,     # объём < 90% от size0 = "проедают"
                 iceberg_recovery: float = 0.9,  # вернулся к 90% = айсберг
                 iceberg_min_eats: int = 2,      # минимум съеданий-восстановлений
                 breakout_min_hits: int = 3):
        self.k = k
        self.min_size = min_size
        self.hold_min = hold_min_updates
        self.pull_stay_bps = pull_stay_bps
        self.eat_thr = eat_threshold
        self.ice_rec = iceberg_recovery
        self.ice_min = iceberg_min_eats
        self.bo_hits = breakout_min_hits
        self.walls = {"bid": defaultdict(dict), "ask": defaultdict(dict)}
        # tape-статистика
        self.prints = deque(maxlen=600)  # ~10 мин при 1с
        self.features = {}
        self.day_high = None
        self.day_low = None
        self._c = 0

    # ---------- walls ----------
    def _update_walls(self, side, levels, cur_trades):
        store = self.walls[side]
        alive = set()
        events = []
        for idx in range(min(5, len(levels))):
            p, s = float(levels[idx][0]), float(levels[idx][1])
            others = [x[1] for l, x in enumerate(levels) if l != idx]
            ref = sum(others) / len(others) if others else 0
            ratio = s / ref if ref else 0
            if not (ref and s >= max(self.k * ref, self.min_size)):
                continue
            alive.add(p)
            w = store.get(p)
            if not w:
                store[p] = {"size0": s, "size": s, "age": 1, "hits": 0,
                            "eaten_lots": 0, "eats": 0, "iceberg": False,
                            "peak_eat": 0.0, "last": self._c}
                continue
            w["last"] = self._c
            w["age"] += 1
            if s < w["size"] * 0.9:          # объём просел — атака
                w["hits"] += 1
                w["eaten_lots"] += w["size"] - s
                w["peak_eat"] = max(w["peak_eat"], 1 - s / w["size0"])
                w["size"] = s
                w["eats"] += 1
            elif s > w["size"]:              # объём подрос
                if (w["eats"] >= self.ice_min and
                        s >= self.ice_rec * w["size0"] and
                        cur_trades > 0):
                    w["iceberg"] = True      # восстановился после съеданий = айсберг
                w["size"] = s
        # исчезнувшие
        for p in list(store.keys()):
            if p in alive:
                continue
            w = store.pop(p)
            eaten_pct = w["eaten_lots"] / max(1.0, w["size0"])
            stay_ok = self.last_mid is None or abs(self.last_mid - p) / self.last_mid * 1e4 <= self.pull_stay_bps
            if w["eats"] >= self.ice_min and eaten_pct < 0.3:
                events.append({"kind": "vanish_iceberg", "side": side, "price": p, "w": w})
                continue  # айсберг просто убрали котировку — не сигнал
            if eaten_pct >= 0.3 and w["hits"] > 0:
                events.append({"kind": "eaten", "side": side, "price": p, "w": w,
                               "eaten_pct": eaten_pct})
            elif stay_ok and w["age"] >= self.hold_min:
                events.append({"kind": "pulled", "side": side, "price": p, "w": w})
        return events

    # ---------- features ----------
    def _compute_features(self, snap):
        tape = self.prints
        now = tp_now = None
        mids = getattr(self, "_mid_hist", deque(maxlen=600))
        mids.append((self._c, snap["mid"]))
        self._mid_hist = mids

        buys = sum(t["size"] for t in tape if t["side"] == "buy")
        sells = sum(t["size"] for t in tape if t["side"] == "sell")
        cvd = buys - sells
        span = max(1, self._c - getattr(self, "_feat_c0", self._c))
        self._feat_c0 = self._c

        # тренд 5 мин: mid сейчас vs ~5 мин назад (при 1 обновлении/2с ~150 обновлений)
        back = mids[0][1] if len(mids) < 150 else mids[-150][1]
        trend_bps = (snap["mid"] / back - 1) * 1e4 if back else 0.0

        if self.day_high is None or snap["mid"] > self.day_high:
            self.day_high = snap["mid"]
        if self.day_low is None or snap["mid"] < self.day_low:
            self.day_low = snap["mid"]
        rng = self.day_high - self.day_low
        day_pos = (snap["mid"] - self.day_low) / rng if rng else 0.5

        b3 = sum(s for _, s in snap["bids"][:3])
        a3 = sum(s for _, s in snap["asks"][:3])
        imb3 = (b3 - a3) / (b3 + a3) if b3 + a3 else 0.0

        self.features = {
            "cvd": cvd,
            "flow_imbalance": cvd / (buys + sells) if buys + sells else 0.0,
            "trend_5m_bps": round(trend_bps, 1),
            "day_range_pos": round(day_pos, 2),
            "spread_bps": round(snap["spread"] / snap["mid"] * 1e4, 2) if snap["mid"] else 0,
            "imbalance_top3": round(imb3, 2),
            "n_prints": len(tape),
        }

    # ---------- main ----------
    def update(self, snap, tape):
        self._c += 1
        for t in tape:
            if t.get("side") in ("buy", "sell"):
                self.prints.append(t)
        self.last_mid = snap["mid"]
        cur_trades = len(tape)

        ev_bid = self._update_walls("bid", snap["bids"], cur_trades)
        ev_ask = self._update_walls("ask", snap["asks"], cur_trades)
        self._compute_features(snap)

        signals = []
        for ev in ev_bid + ev_ask:
            side, w = ev["side"], ev["w"]
            if ev["kind"] == "eaten" and w["hits"] >= self.bo_hits:
                action = "sell" if side == "bid" else "buy"   # пробой
                kind = "breakout"
            elif ev["kind"] == "pulled":
                action = "sell" if side == "bid" else "buy"
                kind = "pull"
            else:
                continue
            signals.append(self._sig(action, kind, side, ev["price"], w))
        # rejection: 1-е касание живой стены (hits == 0 -> после первого удара hits=1)
        # и айсберг-стены на любом касании (усиленный сигнал)
        for side in ("bid", "ask"):
            for p, w in self.walls[side].items():
                near = abs(snap["mid"] - p) / snap["mid"] * 1e4 <= 30
                if not near:
                    continue
                if w["hits"] == 1 and w["eats"] == 1:   # первое касание случилось
                    signals.append(self._sig(
                        "buy" if side == "bid" else "sell", "rejection", side, p, w))
                elif w["iceberg"] and w["hits"] >= 2:
                    signals.append(self._sig(
                        "buy" if side == "bid" else "sell", "rejection_iceberg", side, p, w))
        report = {"signals": signals,
                  "walls_alive": {s: len(self.walls[s]) for s in self.walls},
                  "features": self.features}
        self.last_report = report
        return report

    def _sig(self, action, kind, wall_side, price, w):
        f = dict(self.features)
        f.update({"wall_ratio": round(w["size0"] / max(1.0, w["eaten_lots"] + w["size0"]) * 3, 1),
                  "wall_size": w["size"], "wall_size0": w["size0"],
                  "wall_eaten_pct": round(w["eaten_lots"] / max(1.0, w["size0"]), 2),
                  "wall_hits": w["hits"], "wall_age": w["age"],
                  "wall_is_iceberg": w["iceberg"]})
        return {"action": action, "kind": kind, "wall_side": wall_side,
                "wall_price": price, "features": f, "score": 0.5 if action == "buy" else -0.5}


class SmartModel:
    """Decision-интерфейс для trader.py: сигналы SmartWallTracker, гейт вне."""
    name = "smart-walls-v2"

    def __init__(self, tracker: SmartWallTracker = None):
        self.tracker = tracker or SmartWallTracker()
        self.last_block = -1
        self.ttl = 3

    def decide(self, snap):
        rep = self.tracker.update(snap, snap["tape"])
        sig = rep["signals"][-1] if rep["signals"] else None
        self.last_block += 1
        if sig is None or self.last_block - getattr(self, "_sig_block", -99) > self.ttl:
            if sig:
                self._sig_block = self.last_block
            return {"action": "hold", "score": 0.0,
                    "probabilities": {"buy": 0.5, "sell": 0.5, "hold": 0},
                    "confidence": 0, "signal": sig, "gate": None, "latency_ms": 0.1}
        self._sig_block = self.last_block
        score = 0.5 if sig["action"] == "buy" else -0.5
        return {"action": sig["action"], "score": score,
                "probabilities": {"buy": max(0, 0.5 + score / 2),
                                  "sell": max(0, 0.5 - score / 2), "hold": 0},
                "confidence": abs(score), "signal": sig, "gate": None, "latency_ms": 0.1}
