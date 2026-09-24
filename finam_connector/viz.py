"""FinamViz — быстрые графики над данными коннектора.

    from finam_connector.viz import FinamViz
    viz = FinamViz(analytics)
    viz.candlestick("SBER@MISX", "H1", indicators=True, out="sber.png")
    viz.volume_profile_chart("SBER@MISX", out="vp.png")
    viz.wall_map(live=True, out="walls.png")
"""
import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

UP, DOWN = "#2a9d8f", "#e76f51"


def _dt(ts):
    return datetime.fromtimestamp(ts)


class FinamViz:
    def __init__(self, analytics):
        self.an = analytics

    def candlestick(self, symbol, timeframe="H1", n=120, indicators=True, out=None):
        bars = self.an._get_bars(symbol, timeframe)[-n:]
        fig, ax = plt.subplots(figsize=(14, 6))
        for i, b in enumerate(bars):
            c = UP if b["close"] >= b["open"] else DOWN
            ax.plot([i, i], [b["low"], b["high"]], color=c, lw=0.7)
            ax.add_patch(plt.Rectangle((i - 0.35, min(b["open"], b["close"])), 0.7,
                                       abs(b["close"] - b["open"]) or 0.001, color=c))
        if indicators:
            closes = [b["close"] for b in bars]
            def _ema(closes, n):
                k = 2 / (n + 1)
                s = [closes[0]]
                for c in closes[1:]:
                    s.append(c * k + s[-1] * (1 - k))
                return s
            ax.plot(range(len(bars)), _ema(closes, 20), lw=1, color="#457b9d", label="EMA20")
            ax.plot(range(len(bars)), _ema(closes, 50), lw=1, color="#e9c46a", label="EMA50")
            ax.legend()
        ax.set_title(f"{symbol} {timeframe}")
        ax.grid(alpha=0.2)
        step = max(1, len(bars) // 8)
        ax.set_xticks(range(0, len(bars), step))
        ax.set_xticklabels([_dt(bars[i]["ts"]).strftime("%d.%m %H:%M") for i in range(0, len(bars), step)],
                           fontsize=8)
        plt.tight_layout()
        out = out or f"{symbol.replace('@','_')}_candles.png"
        plt.savefig(out, dpi=110)
        plt.close()
        return out

    def volume_profile_chart(self, symbol, timeframe="D1", out=None):
        vp = self.an.volume_profile(symbol, timeframe)
        if "error" in vp:
            return vp
        fig, ax = plt.subplots(figsize=(8, 7))
        prices = [p["price"] for p in vp["profile"]]
        vols = [p["vol"] for p in vp["profile"]]
        colors = [UP if vp["va_low"] <= p <= vp["va_high"] else "#bbb" for p in prices]
        colors[prices.index(vp["poc"])] = "#e9c46a"
        ax.barh(prices, vols, height=(prices[1]-prices[0]) * 0.9, color=colors)
        ax.axhline(vp["poc"], color="#e9c46a", ls="--", label=f"POC {vp['poc']}")
        ax.axhline(vp["va_high"], color="grey", ls=":", label=f"VA {vp['va_low']}–{vp['va_high']}")
        ax.axhline(vp["va_low"], color="grey", ls=":")
        ax.legend()
        ax.set_title(f"Volume Profile {symbol} {timeframe}")
        plt.tight_layout()
        out = out or f"{symbol.replace('@','_')}_vp.png"
        plt.savefig(out, dpi=110)
        plt.close()
        return out

    def wall_map(self, out=None):
        """Live: текущие стены (требует analytics.start_live)."""
        snap = self.an.walls_snapshot()
        fig, ax = plt.subplots(figsize=(10, 7))
        for w in snap["walls"]:
            color = "#2a9d8f" if w["side"] == "bid" else "#e76f51"
            marker = "D" if w["iceberg"] else "o"
            size = 40 + min(w["size"] / 100, 600)
            ax.scatter(0, w["price"], s=size, c=color, marker=marker, alpha=0.7)
            ax.annotate(f"{w['size']:.0f}{' ICE' if w['iceberg'] else ''}"
                        f" h{w['hits']} a{w['age']}",
                        (0.02, w["price"]), fontsize=8)
        ax.set_xlim(-0.5, 1)
        ax.set_title("Wall map (зелёный=bid, красный=ask, ромб=айсберг)")
        ax.get_xaxis().set_visible(False)
        plt.tight_layout()
        out = out or "wall_map.png"
        plt.savefig(out, dpi=110)
        plt.close()
        return out

    def full_report(self, symbol, out="report.png"):
        """4 панели: свечи+EMA, volume profile, RSI, стена/поток."""
        fig = plt.figure(figsize=(16, 12))
        gs = fig.add_gridspec(2, 2, hspace=0.3)
        # candles
        bars = self.an._get_bars(symbol, "H1")[-120:]
        ax = fig.add_subplot(gs[0, 0])
        closes = [b["close"] for b in bars]
        ax.plot(range(len(bars)), closes, lw=0.8, color="#264653")
        ax.set_title(f"{symbol} H1 close")
        ax.grid(alpha=0.2)
        # VP
        ax = fig.add_subplot(gs[0, 1])
        vp = self.an.volume_profile(symbol, "D1")
        if "profile" in vp:
            ax.barh([p["price"] for p in vp["profile"]],
                    [p["vol"] for p in vp["profile"]], height=2, color="#457b9d")
            ax.axhline(vp["poc"], color="#e9c46a", ls="--")
        ax.set_title("Volume Profile D1")
        # RSI
        ax = fig.add_subplot(gs[1, 0])
        from indicators import Indicators
        ind_obj = Indicators([{"c": b["close"], "h": b["high"], "l": b["low"],
                               "o": b["open"], "v": b["volume"], "ts": b.get("ts", 0)}
                              for b in bars])
        rsi_series = [_rsi_series([b["close"] for b in bars[:k+1]])[-1]
                      for k in range(14, len(bars))]
        ax.plot(range(len(rsi_series)), rsi_series, color="#e76f51")
        ax.axhline(70, ls=":", color="grey"); ax.axhline(30, ls=":", color="grey")
        ax.set_title("RSI14 H1")
        # flow
        ax = fig.add_subplot(gs[1, 1])
        ax.axis("off")
        ind = self.an.indicators(symbol, "H1")
        flow = self.an.flow_snapshot() if self.an._live else {}
        txt = f"Индикаторы {symbol}:\n{json.dumps(ind, ensure_ascii=False, indent=1)}\n"
        if flow:
            txt += f"\nFlow: {json.dumps(flow)}"
        ax.text(0.02, 0.95, txt, transform=ax.transAxes, fontsize=8,
                verticalalignment="top", fontfamily="monospace")
        plt.savefig(out, dpi=110, bbox_inches="tight")
        plt.close()
        return out


def _ema_series(closes, n):
    k = 2 / (n + 1)
    s = [closes[0]]
    for c in closes[1:]:
        s.append(c * k + s[-1] * (1 - k))
    return s


def _rsi_series(closes, n=14):
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas[-(n+1):]]
    losses = [max(-d, 0) for d in deltas[-(n+1):]]
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n-1) + gains[i]) / n
        al = (al * (n-1) + losses[i]) / n
    if al == 0:
        return [100.0]
    rs = ag / al
    return [100 - 100 / (1 + rs)]



