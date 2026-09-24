"""Демо-визуализация finam-connector: одна картинка со всеми панелями.

Собирает: свечи D1 + H1, volume profile, RSI, стены/данные дня из живого сбора,
ленту (из NDJSON коллектора). Выход: demo_dashboard.png
"""
import sys, os, json
from datetime import datetime

sys.path.insert(0, "/home/user/.hermes/scripts-dev/finam-connector")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from finam_connector import FinamConnector, Analytics

UP, DOWN = "#2a9d8f", "#e76f51"
ACCENT = "#e9c46a"
GRID = dict(alpha=0.15, color="w")

plt.rcParams.update({
    "figure.facecolor": "#10151c", "axes.facecolor": "#161d26",
    "axes.edgecolor": "#2a3542", "axes.labelcolor": "#c9d3dd",
    "xtick.color": "#8fa0b0", "ytick.color": "#8fa0b0",
    "text.color": "#e6edf3", "font.size": 9,
    "axes.titlecolor": "#e6edf3", "axes.titleweight": "bold",
})

# ---------- данные из коннектора ----------
fc = FinamConnector(token=os.environ["FINAM_TOKEN"])
an = Analytics(fc)
d1 = an.fc.bars("SBER@MISX", "D1", "01.06.2026")
h1 = an.fc.bars("SBER@MISX", "H1", "15.09.2026")
vp = an.volume_profile("SBER@MISX", "D1")
ind_d1 = an.indicators("SBER@MISX", "D1")
fc.close()

# ---------- лента из сегодняшнего коллектора ----------
tape = []
tape_file = "/home/user/.hermes/scripts-dev/moex-spread/data/SBER-MISX-2026-09-24.ndjson"
if os.path.exists(tape_file):
    with open(tape_file) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if "q" not in r:
                tape.append(r)
# последние 2 часа
if tape:
    t_cut = tape[-1]["t"][:16]
    tape = [r for r in tape if r["t"] >= t_cut]

def ema(vals, n):
    k = 2 / (n + 1)
    s = [vals[0]]
    for v in vals[1:]:
        s.append(v * k + s[-1] * (1 - k))
    return s

def rsi_series(closes, n=14):
    out = [None] * n
    for i in range(n, len(closes)):
        gains = losses = 0.0
        for j in range(i - n + 1, i + 1):
            d = closes[j] - closes[j - 1]
            gains += max(d, 0) / n
            losses += max(-d, 0) / n
        out.append(100 - 100 / (1 + gains / losses) if losses else 100.0)
    return out

fig = plt.figure(figsize=(18, 11))
gs = fig.add_gridspec(3, 3, hspace=0.34, wspace=0.25,
                      width_ratios=[1.3, 1, 1])

# ===== 1. Свечи D1 + EMA + VP-уровни =====
ax = fig.add_subplot(gs[0, :2])
closes = [b["close"] for b in d1]
e20, e50 = ema(closes, 20), ema(closes, 50)
for i, b in enumerate(d1):
    c = UP if b["close"] >= b["open"] else DOWN
    ax.plot([i, i], [b["low"], b["high"]], color=c, lw=0.8)
    ax.add_patch(plt.Rectangle((i - 0.35, min(b["open"], b["close"])), 0.7,
                 max(abs(b["close"] - b["open"]), 0.001), color=c))
ax.plot(range(len(d1)), e20, lw=1.2, color="#4cc9f0", label="EMA20")
ax.plot(range(len(d1)), e50, lw=1.2, color=ACCENT, label="EMA50")
if "poc" in vp:
    ax.axhline(vp["poc"], color="#f4a261", ls="--", lw=1.1,
               label=f"POC {vp['poc']}")
    ax.axhspan(vp["va_low"], vp["va_high"], color="#f4a261", alpha=0.07)
    ax.axhline(vp["va_low"], color="#8d99ae", ls=":", lw=0.8)
    ax.axhline(vp["va_high"], color="#8d99ae", ls=":", lw=0.8)
ax.set_title(f"SBER  D1  |  RSI {ind_d1['rsi14']}  ATR {ind_d1['atr14']}  "
             f"EMA-cross: {ind_d1['ema20_50_cross']}")
ax.legend(loc="upper left", fontsize=8, framealpha=0.3)
ax.grid(True, **GRID)
step = max(1, len(d1) // 10)
ax.set_xticks(range(0, len(d1), step))
ax.set_xticklabels([datetime.fromtimestamp(d1[i]["ts"]).strftime("%d.%m")
                    for i in range(0, len(d1), step)], fontsize=7)

# ===== 2. Volume Profile D1 (горизонтальный) =====
ax = fig.add_subplot(gs[0, 2])
prof = vp.get("profile", [])
prices = [p["price"] for p in prof]
vols = [p["vol"] for p in prof]
colors = [ACCENT if abs(p - vp["poc"]) < (prices[1] - prices[0])
          else ("#4a6274" if vp["va_low"] <= p <= vp["va_high"] else "#2d3a47")
          for p in prices]
ax.barh(prices, vols, height=(prices[1] - prices[0]) * 0.92, color=colors)
ax.axhline(vp["poc"], color=ACCENT, ls="--", lw=1)
ax.set_title(f"Volume Profile D1 | POC {vp['poc']} | VA {vp['va_low']}–{vp['va_high']}")
ax.grid(True, axis="x", **GRID)
ax.tick_params(labelsize=7)

# ===== 3. Свечи H1 (2 недели) =====
ax = fig.add_subplot(gs[1, 0])
for i, b in enumerate(h1):
    c = UP if b["close"] >= b["open"] else DOWN
    ax.plot([i, i], [b["low"], b["high"]], color=c, lw=0.6)
    ax.add_patch(plt.Rectangle((i - 0.3, min(b["open"], b["close"])), 0.6,
                 max(abs(b["close"] - b["open"]), 0.001), color=c))
ch = ema([b["close"] for b in h1], 20)
ax.plot(range(len(h1)), ch, lw=1, color="#4cc9f0")
ax.set_title(f"SBER H1 ({len(h1)} баров, 15.09→)")
ax.grid(True, **GRID)
step = max(1, len(h1) // 6)
ax.set_xticks(range(0, len(h1), step))
ax.set_xticklabels([datetime.fromtimestamp(h1[i]["ts"]).strftime("%d.%m %H:%M")
                    for i in range(0, len(h1), step)], fontsize=6, rotation=30)

# ===== 4. Лента дня (цена + buy/sell точки) =====
ax = fig.add_subplot(gs[1, 1:])
if tape:
    ts = [datetime.fromisoformat(r["t"]) for r in tape]
    px = [r["p"] for r in tape]
    dirs = [r["d"] for r in tape]
    sizes = [min(r["s"], 500) for r in tape]
    ax.scatter([t for t, d in zip(ts, dirs) if d > 0],
               [p for p, d in zip(px, dirs) if d > 0],
               c=UP, s=6, alpha=0.55, label="buy prints")
    ax.scatter([t for t, d in zip(ts, dirs) if d < 0],
               [p for p, d in zip(px, dirs) if d < 0],
               c=DOWN, s=6, alpha=0.55, label="sell prints")
    ax.plot(ts, px, color="#4cc9f0", lw=0.6, alpha=0.7)
    buys = sum(r["s"] for r in tape if r["d"] > 0)
    sells = sum(r["s"] for r in tape if r["d"] < 0)
    cvd = buys - sells
    ax.set_title(f"Лента SBER сегодня ({len(tape)} сделок, ~последний час) | "
                 f"CVD {cvd:+.0f} лотов | buy/sell {buys:.0f}/{sells:.0f}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.legend(loc="upper left", fontsize=8, framealpha=0.3)
else:
    ax.text(0.5, 0.5, "лента недоступна", ha="center", va="center",
            transform=ax.transAxes)
ax.grid(True, **GRID)

# ===== 5. RSI D1 =====
ax = fig.add_subplot(gs[2, 0])
rs = rsi_series(closes)
ax.plot(range(len(rs)), rs, color="#b388eb", lw=1.1)
ax.axhline(70, color=DOWN, ls=":", lw=0.8)
ax.axhline(30, color=UP, ls=":", lw=0.8)
ax.axhline(50, color="#8d99ae", ls=":", lw=0.5)
ax.set_ylim(10, 90)
ax.set_title("RSI(14) D1")
ax.grid(True, **GRID)

# ===== 6. Поток: накопленный CVD за день =====
ax = fig.add_subplot(gs[2, 1:])
if tape:
    cvd, xs, ys = 0, [], []
    for r in tape:
        cvd += r["s"] * r["d"]
        xs.append(datetime.fromisoformat(r["t"]))
        ys.append(cvd)
    ax.fill_between(xs, ys, 0, color="#4cc9f0", alpha=0.25)
    ax.plot(xs, ys, color="#4cc9f0", lw=1)
    ax.axhline(0, color="#8d99ae", lw=0.5)
    ax.set_title(f"Накопленный CVD за час ({cvd:+.0f} лотов) —"
                 f" {'покупатели давят' if cvd > 0 else 'продавцы давят'}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
else:
    ax.text(0.5, 0.5, "нет ленты", ha="center", transform=ax.transAxes)
ax.grid(True, **GRID)

fig.suptitle("finam-connector — демо-дашборд SBER@MISX · " +
             datetime.now().strftime("%d.%m.%Y %H:%M"),
             fontsize=13, color="#e6edf3", y=0.995)
out = "/home/user/.hermes/scripts-dev/finam-connector/demo_dashboard.png"
plt.savefig(out, dpi=115, bbox_inches="tight", facecolor="#10151c")
print("saved", out)
