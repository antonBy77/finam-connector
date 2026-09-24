import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""Быстрый smoke-тест finam-connector (кэш недостающих баров исключён)."""
from finam_connector import FinamConnector, Analytics, FinamViz

fc = FinamConnector(token="tapi_sk_lb3LL8mmRzajUal3zErImg")
an = Analytics(fc)
ind = an.indicators("SBER@MISX", "D1")
print("IND:", {k: ind.get(k) for k in ("rsi14", "ema20_50_cross", "atr14")}, flush=True)
vp = an.volume_profile("SBER@MISX", "D1")
print("POC:", vp.get("poc"), "| VA:", vp.get("va_low"), "-", vp.get("va_high"), flush=True)
viz = FinamViz(an)
print("candles:", viz.candlestick("SBER@MISX", "D1", out="/tmp/fc_candles.png"), flush=True)
print("vp:", viz.volume_profile_chart("SBER@MISX", out="/tmp/fc_vp.png"), flush=True)
fc.close()
print("SMOKE OK")
