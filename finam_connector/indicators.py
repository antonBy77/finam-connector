#!/usr/bin/env python3
"""
MOEX Indicators — расчёт технических индикаторов на барах FinamPy.
Без внешних зависимостей. Чистый Python + numpy (если есть).

Использование:
  from indicators import Indicators
  ind = Indicators(bars)  # bars = list of dicts {o, h, l, c, v, ts}
  ema8 = ind.ema(8)
  rsi14 = ind.rsi(14)
  bb = ind.bollinger(20, 2)
"""

import math
from collections import namedtuple

Bar = namedtuple('Bar', ['o', 'h', 'l', 'c', 'v', 'ts'])


class Indicators:
    """Технические индикаторы на массиве баров."""
    
    def __init__(self, bars):
        """
        bars: list of Bar или list of dict {o,h,l,c,v,ts}
        """
        if bars and isinstance(bars[0], dict):
            self.bars = [Bar(b['c'], b['h'], b['l'], b['c'], b['v'], b.get('ts', 0)) for b in bars]
        else:
            self.bars = bars
        
        self._closes = [b.c for b in self.bars]
        self._highs = [b.h for b in self.bars]
        self._lows = [b.l for b in self.bars]
        self._volumes = [b.v for b in self.bars]
        self.n = len(self.bars)
    
    # ── Базовые ──────────────────────────────────────────
    
    def sma(self, period, data=None):
        """Simple Moving Average"""
        if data is None:
            data = self._closes
        result = [None] * len(data)
        for i in range(period - 1, len(data)):
            result[i] = sum(data[i - period + 1:i + 1]) / period
        return result
    
    def ema(self, period, data=None):
        """Exponential Moving Average"""
        if data is None:
            data = self._closes
        if len(data) < period:
            return [None] * len(data)
        
        result = [None] * len(data)
        k = 2.0 / (period + 1)
        
        # Начальное значение = SMA
        result[period - 1] = sum(data[:period]) / period
        
        for i in range(period, len(data)):
            result[i] = data[i] * k + result[i - 1] * (1 - k)
        
        return result
    
    def rsi(self, period=14):
        """Relative Strength Index"""
        if self.n < period + 1:
            return [None] * self.n
        
        result = [None] * self.n
        gains = []
        losses = []
        
        for i in range(1, self.n):
            change = self._closes[i] - self._closes[i - 1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        
        # Первый RSI = среднее за period
        if len(gains) < period:
            return result
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        if avg_loss == 0:
            result[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[period] = 100.0 - (100.0 / (1 + rs))
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            
            if avg_loss == 0:
                result[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i + 1] = 100.0 - (100.0 / (1 + rs))
        
        return result
    
    def macd(self, fast=12, slow=26, signal=9):
        """MACD: возвращает (macd_line, signal_line, histogram)"""
        ema_fast = self.ema(fast)
        ema_slow = self.ema(slow)
        
        macd_line = [None] * self.n
        for i in range(self.n):
            if ema_fast[i] is not None and ema_slow[i] is not None:
                macd_line[i] = ema_fast[i] - ema_slow[i]
        
        # Signal line = EMA от MACD
        macd_values = [v if v is not None else 0 for v in macd_line]
        signal_line = self.ema(signal, data=macd_values)
        
        histogram = [None] * self.n
        for i in range(self.n):
            if macd_line[i] is not None and signal_line[i] is not None:
                histogram[i] = macd_line[i] - signal_line[i]
        
        return macd_line, signal_line, histogram
    
    def bollinger(self, period=20, std_mult=2.0):
        """Bollinger Bands: возвращает (upper, middle, lower)"""
        middle = self.sma(period)
        upper = [None] * self.n
        lower = [None] * self.n
        
        for i in range(period - 1, self.n):
            slice_data = self._closes[i - period + 1:i + 1]
            mean = middle[i]
            variance = sum((x - mean) ** 2 for x in slice_data) / period
            std = math.sqrt(variance)
            upper[i] = mean + std_mult * std
            lower[i] = mean - std_mult * std
        
        return upper, middle, lower
    
    def atr(self, period=14):
        """Average True Range"""
        if self.n < 2:
            return [None] * self.n
        
        tr = [None] * self.n
        tr[0] = self._highs[0] - self._lows[0]
        
        for i in range(1, self.n):
            hl = self._highs[i] - self._lows[i]
            hc = abs(self._highs[i] - self._closes[i - 1])
            lc = abs(self._lows[i] - self._closes[i - 1])
            tr[i] = max(hl, hc, lc)
        
        result = [None] * self.n
        if self.n < period + 1:
            return result
        
        result[period] = sum(tr[1:period + 1]) / period
        
        for i in range(period + 1, self.n):
            result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
        
        return result
    
    def vwap(self):
        """Volume Weighted Average Price (кумулятивный за весь период)"""
        result = [None] * self.n
        cum_vp = 0
        cum_vol = 0
        
        for i in range(self.n):
            tp = (self._highs[i] + self._lows[i] + self._closes[i]) / 3
            cum_vp += tp * self._volumes[i]
            cum_vol += self._volumes[i]
            result[i] = cum_vp / cum_vol if cum_vol > 0 else self._closes[i]
        
        return result
    
    def volume_profile(self, bins=20):
        """
        Горизонтальный профиль объёма.
        Возвращает list of (price, volume) отсортированный по price.
        """
        if self.n == 0:
            return []
        
        min_price = min(self._lows)
        max_price = max(self._highs)
        
        if max_price == min_price:
            return [(min_price, sum(self._volumes))]
        
        step = (max_price - min_price) / bins
        profile = [(min_price + i * step + step / 2, 0) for i in range(bins)]
        
        for i in range(self.n):
            for j in range(bins):
                low = min_price + j * step
                high = low + step
                if self._lows[i] <= high and self._highs[i] >= low:
                    # Объём распределяется пропорционально перекрытию
                    overlap = min(self._highs[i], high) - max(self._lows[i], low)
                    total_range = self._highs[i] - self._lows[i]
                    if total_range > 0:
                        vol = self._volumes[i] * (overlap / total_range)
                    else:
                        vol = self._volumes[i] / bins
                    profile[j] = (profile[j][0], profile[j][1] + vol)
        
        return profile
    
    def poc_and_values(self, bins=20):
        """
        Point of Control + Value Area (70% объёма).
        Возвращает: {poc, va_high, va_low, profile}
        """
        profile = self.volume_profile(bins)
        if not profile:
            return None
        
        total_vol = sum(v for _, v in profile)
        poc_price, poc_vol = max(profile, key=lambda x: x[1])
        
        # Value Area = 70% от total volume вокруг POC
        target_vol = total_vol * 0.70
        poc_idx = next(i for i, (p, v) in enumerate(profile) if p == poc_price)
        
        va_vol = profile[poc_idx][1]
        va_low_idx = poc_idx
        va_high_idx = poc_idx
        
        while va_vol < target_vol:
            expand_low = va_low_idx > 0
            expand_high = va_high_idx < len(profile) - 1
            
            if expand_low and expand_high:
                low_vol = profile[va_low_idx - 1][1]
                high_vol = profile[va_high_idx + 1][1]
                if low_vol >= high_vol:
                    va_low_idx -= 1
                    va_vol += profile[va_low_idx][1]
                else:
                    va_high_idx += 1
                    va_vol += profile[va_high_idx][1]
            elif expand_low:
                va_low_idx -= 1
                va_vol += profile[va_low_idx][1]
            elif expand_high:
                va_high_idx += 1
                va_vol += profile[va_high_idx][1]
            else:
                break
        
        return {
            'poc': poc_price,
            'va_high': profile[va_high_idx][0],
            'va_low': profile[va_low_idx][0],
            'total_volume': total_vol,
            'profile': profile
        }
    
    # ── Трендовые сигналы ────────────────────────────────
    
    def ema_cross(self, fast=8, slow=21):
        """
        Пересечение EMA.
        Возвращает: 'BUY' (быстрая > медленной), 'SELL', 'FLAT'
        Последний элемент списка.
        """
        ema_f = self.ema(fast)
        ema_s = self.ema(slow)
        
        result = [None] * self.n
        for i in range(self.n):
            if ema_f[i] is not None and ema_s[i] is not None:
                if ema_f[i] > ema_s[i]:
                    result[i] = 'BUY'
                elif ema_f[i] < ema_s[i]:
                    result[i] = 'SELL'
                else:
                    result[i] = 'FLAT'
        
        return result
    
    def trend(self, fast=8, slow=21):
        """Текущий тренд по EMA кроссу"""
        signals = self.ema_cross(fast, slow)
        for s in reversed(signals):
            if s is not None:
                return s
        return 'FLAT'
    
    def bb_position(self, period=20, std_mult=2.0):
        """
        Позиция цены относительно BB.
        Возвращает: 0-1 (0=lower band, 0.5=middle, 1=upper band)
        """
        upper, middle, lower = self.bollinger(period, std_mult)
        result = [None] * self.n
        
        for i in range(self.n):
            if upper[i] is not None and lower[i] is not None:
                bb_range = upper[i] - lower[i]
                if bb_range > 0:
                    result[i] = (self._closes[i] - lower[i]) / bb_range
                else:
                    result[i] = 0.5
        
        return result
    
    # ── Smart Money Concepts (упрощённые) ────────────────
    
    def order_blocks(self, lookback=10):
        """
        Последний бычий и медвежий Order Block.
        OB = последний нисходящий бар перед импульсным движением вверх (и наоборот).
        Возвращает: {'bull_ob': (price, index), 'bear_ob': (price, index)}
        """
        bull_ob = None
        bear_ob = None
        
        for i in range(1, min(self.n - 1, lookback + 1)):
            idx = self.n - 1 - i
            
            # Бычий OB: нисходящий бар (close < open) перед движением вверх
            if self._closes[idx] < self.bars[idx].o:  # нисходящий
                # Проверяем что после него было движение вверх
                if idx + 1 < self.n:
                    move_up = self._closes[idx + 1] - self._closes[idx]
                    atr_vals = self.atr(14)
                    atr_val = atr_vals[idx] if atr_vals[idx] else 1
                    if move_up > atr_val * 0.5:  # Импульс > 0.5 ATR
                        bull_ob = (self.bars[idx].o, idx)  # Open нисходящего бара
            
            # Медвежий OB: восходящий бар (close > open) перед движением вниз
            if self._closes[idx] > self.bars[idx].o:  # восходящий
                if idx + 1 < self.n:
                    move_down = self._closes[idx] - self._closes[idx + 1]
                    atr_vals = self.atr(14)
                    atr_val = atr_vals[idx] if atr_vals[idx] else 1
                    if move_down > atr_val * 0.5:
                        bear_ob = (self.bars[idx].o, idx)
        
        return {'bull_ob': bull_ob, 'bear_ob': bear_ob}
    
    def fvg(self):
        """
        Fair Value Gaps — ценовые разрывы.
        Возвращает list of {type, high, low, index}
        """
        gaps = []
        for i in range(2, self.n):
            # Бычий FVG: low[i] > high[i-2] (пропуск вверх)
            if self._lows[i] > self._highs[i - 2]:
                gaps.append({
                    'type': 'bull',
                    'low': self._highs[i - 2],
                    'high': self._lows[i],
                    'index': i - 1
                })
            # Медвежий FVG: high[i] < low[i-2] (пропуск вниз)
            if self._highs[i] < self._lows[i - 2]:
                gaps.append({
                    'type': 'bear',
                    'low': self._highs[i],
                    'high': self._lows[i - 2],
                    'index': i - 1
                })
        
        return gaps
    
    # ── Композитный сигнал ───────────────────────────────
    
    def signal_score(self):
        """
        Композитная оценка от -100 до +100.
        Положительная = бычий, отрицательная = медвежий.
        
        Веса:
          EMA cross: 30%
          RSI: 20%
          BB position: 15%
          MACD: 20%
          Volume trend: 15%
        """
        if self.n < 30:
            return 0
        
        score = 0
        
        # 1. EMA Cross (30 pts)
        trend = self.trend(8, 21)
        if trend == 'BUY':
            score += 30
        elif trend == 'SELL':
            score -= 30
        
        # 2. RSI (20 pts)
        rsi_vals = self.rsi(14)
        rsi_now = None
        for v in reversed(rsi_vals):
            if v is not None:
                rsi_now = v
                break
        
        if rsi_now is not None:
            if rsi_now > 70:
                score -= 15  # перекупленность
            elif rsi_now < 30:
                score += 15  # перепроданность
            elif rsi_now > 50:
                score += 10
            else:
                score -= 10
        
        # 3. BB position (15 pts)
        bb_pos = self.bb_position(20, 2.0)
        bb_now = None
        for v in reversed(bb_pos):
            if v is not None:
                bb_now = v
                break
        
        if bb_now is not None:
            if bb_now > 0.85:
                score -= 10  # у верхней полосы
            elif bb_now < 0.15:
                score += 10  # у нижней полосы
            elif bb_now > 0.6:
                score += 5
            else:
                score -= 5
        
        # 4. MACD (20 pts)
        macd_line, signal_line, hist = self.macd(12, 26, 9)
        hist_now = None
        for v in reversed(hist):
            if v is not None:
                hist_now = v
                break
        
        if hist_now is not None:
            if hist_now > 0:
                score += 10
            else:
                score -= 10
        
        # MACD direction
        prev_hist = None
        for v in hist[-5:]:
            if v is not None:
                prev_hist = v
                break
        if hist_now is not None and prev_hist is not None:
            if hist_now > prev_hist:
                score += 10  # растущий
            else:
                score -= 10
        
        # 5. Volume trend (15 pts)
        if self.n >= 10:
            vol_recent = sum(self._volumes[-3:]) / 3
            vol_avg = sum(self._volumes[-10:]) / 10
            if vol_avg > 0:
                vol_ratio = vol_recent / vol_avg
                if vol_ratio > 1.5 and trend == 'BUY':
                    score += 15  # объём подтверждает тренд
                elif vol_ratio > 1.5 and trend == 'SELL':
                    score -= 15
                elif vol_ratio < 0.5:
                    score -= 5  # низкий объём = неуверенность
        
        return max(-100, min(100, score))
