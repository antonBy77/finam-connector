# finam-connector

Единый Python-коннектор к Finam Trade API (MOEX): gRPC-стриминг + unary-запросы +
аналитика на лету + визуализация. Собрано из наработок конкурса Finam Arena
(июнь-июль 2026) и проекта moex-wall-trader.

## Установка

```bash
git clone <repo>
# зависимости
pip install FinamPy grpcio protobuf googleapis-common-protos matplotlib
export FINAM_TOKEN="ваш API-токен Finam"   # секрет только через env!
```

Требуется библиотека [FinamPy](https://github.com/cia76/FinamPy) в `/home/user/FinamPy`
или `/opt/FinamPy` (путь настраивается в `client.py:_FINAMPY_PATHS`).

## Quickstart

```python
from finam_connector import FinamConnector, Analytics, FinamViz

fc = FinamConnector(token=os.environ["FINAM_TOKEN"], account_id="...")

# Данные (unary)
fc.quote("SBER@MISX")                    # котировка
fc.orderbook("SBER@MISX")                # весь стакан
fc.latest_trades("SBER@MISX", 100)       # лента сделок с side
an = Analytics(fc)
an.load_bars("SBER@MISX", "H1", "20.09.2026")

# Стриминг (не тратит rate limit)
fc.stream_trades("SBER@MISX", print)     # каждый тик
fc.stream_orderbook("SBER@MISX", print)  # каждый апдейт стакана

# Расчёты (наследие Arena)
an.indicators("SBER@MISX", "D1")         # RSI/MACD/EMA-cross/BB/ATR/trend
an.volume_profile("SBER@MISX", "D1")     # POC / Value Area
an.start_live("SBER@MISX")               # включает стримы
an.walls_snapshot()                      # стены + айсберги + сигналы
an.flow_snapshot()                       # CVD / дисбаланс потока
an.gate_state("SBER@MISX")               # свод для Laya/Jev-гейта

# Визуализация
viz = FinamViz(an)
viz.candlestick("SBER@MISX", "H1", out="candles.png")
viz.volume_profile_chart("SBER@MISX", out="vp.png")
viz.wall_map(out="walls.png")
viz.full_report("SBER@MISX", out="report.png")

# Демо-дашборд 6-в-1: свечи D1/H1 + VP + лента дня + RSI + CVD
# (см. examples/demo_dashboard.py)
```

Пример дашборда собирается командой `python3 examples/demo_dashboard.py` —
свечи с EMA и POC, volume profile, лента дня с buy/sell-принтами и CVD.

## Проверено в бою (24.09.2026)

- **Стриминг:** SBER весь день, тысячи тиков без пропусков, задержка p50 0.57с.
  Полный дневной датасет: каждая сделка + стакан 2с в NDJSON.
- **Edge стен подтверждён на живом полном стакане:** стены (≥3× соседей) держатся
  82.7% через 60с против 76.2% обычных уровней — +6.5%, z=13.2.
- **Бэктест rejection с мейкер-мейкером:** 221 сделка за день, +9.37 ₽/лот, win 89%
  (детали и A/B с гейтом — в [moex-wall-trader](https://github.com/antonBy77/moex-wall-trader)).

## Состав

| Модуль | Что умеет |
|---|---|
| `client.py` | FinamConnector: REST+gRPC, rate limiter, unary+stream, ордера |
| `analytics.py` | Analytics: индикаторы, volume profile, стены/айсберги, flow, gate_state |
| `walls_v2.py` | SmartWallTracker: стены, hit-счётчик, пробои, айсберги, фичи |
| `indicators.py` | Класс Indicators (Arena): SMA/EMA/RSI/MACD/BB/ATR/VWAP/OB/FVG |
| `delta_analyzer.py` | Delta/cvd/абсорбция (Arena) |
| `viz.py` | FinamViz: свечи, VP, wall map, полный отчёт |

## Известные ограничения (проверено 24.09.2026)

- Rate limit ~200 req/min суммарно; стримы не расходуют
- `TimeFrame` enum: `TIME_FRAME_W` (не W1), полный: M1..M30, H1, H2, H4, H8, D, W, MN, QR
- Вне торговых часов (будни 10:00-23:50 MSK) стакан пуст, стрим молчит
- Known issue: gRPC Bars может виснуть с некоторых хостов — fallback REST
- Секреты только через env: `FINAM_TOKEN`

## Конкурсы

База готова к новому конкурсу Finam Arena (старт — первые числа месяца,
детали уточняются). Все наработки Arena/2026 интегрированы.
