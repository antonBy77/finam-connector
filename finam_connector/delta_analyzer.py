#!/usr/bin/env python3
"""
Delta Analyzer — анализ потока ордеров из сделок FinamPy.
Использует поле side (BUY/SELL) для расчёта дельты.

Использование:
  from delta_analyzer import DeltaAnalyzer
  da = DeltaAnalyzer(trades)  # trades from FinamPy LatestTrades
  delta = da.cumulative_delta()
  divergence = da.detect_divergence(bars)
"""

from collections import defaultdict


class DeltaAnalyzer:
    """Анализ дельты и ордерфлоу из обезличенных сделок."""
    
    def __init__(self, trades):
        """
        trades: list of dict {price, size, side, ts}
          side: 'BUY' или 'SELL' (или 1/2 из FinamPy side_pb2)
        """
        self.trades = []
        for t in trades:
            side = t.get('side', '')
            if isinstance(side, int):
                side = 'BUY' if side == 1 else 'SELL'
            self.trades.append({
                'price': float(t['price']),
                'size': float(t.get('size', t.get('quantity', 0))),
                'side': str(side).upper(),
                'ts': t.get('ts', 0)
            })
        self.n = len(self.trades)
    
    def cumulative_delta(self):
        """
        Кумулятивная дельта: buy_volume - sell_volume
        Возвращает list of {ts, delta, cum_delta, buy_vol, sell_vol}
        """
        result = []
        cum = 0
        
        for t in self.trades:
            buy_vol = t['size'] if t['side'] == 'BUY' else 0
            sell_vol = t['size'] if t['side'] == 'SELL' else 0
            delta = buy_vol - sell_vol
            cum += delta
            
            result.append({
                'ts': t['ts'],
                'price': t['price'],
                'delta': delta,
                'cum_delta': cum,
                'buy_vol': buy_vol,
                'sell_vol': sell_vol
            })
        
        return result
    
    def delta_summary(self):
        """Сводка по дельте за весь период."""
        total_buy = sum(t['size'] for t in self.trades if t['side'] == 'BUY')
        total_sell = sum(t['size'] for t in self.trades if t['side'] == 'SELL')
        total_vol = total_buy + total_sell
        
        return {
            'total_buy': total_buy,
            'total_sell': total_sell,
            'delta': total_buy - total_sell,
            'delta_pct': (total_buy - total_sell) / total_vol * 100 if total_vol > 0 else 0,
            'buy_pct': total_buy / total_vol * 100 if total_vol > 0 else 0,
            'trade_count': self.n,
            'avg_trade_size': total_vol / self.n if self.n > 0 else 0,
        }
    
    def large_trades(self, threshold_pct=95):
        """
        Крупные сделки (выше threshold перцентиля по размеру).
        Возвращает list of large trades.
        """
        if self.n == 0:
            return []
        
        sizes = sorted([t['size'] for t in self.trades])
        idx = int(len(sizes) * threshold_pct / 100)
        threshold = sizes[min(idx, len(sizes) - 1)]
        
        large = [t for t in self.trades if t['size'] >= threshold]
        return large
    
    def large_trades_imbalance(self, threshold_pct=95):
        """Баланс крупных сделок: BUY vs SELL."""
        large = self.large_trades(threshold_pct)
        buy_large = sum(t['size'] for t in large if t['side'] == 'BUY')
        sell_large = sum(t['size'] for t in large if t['side'] == 'SELL')
        total = buy_large + sell_large
        
        return {
            'buy_large': buy_large,
            'sell_large': sell_large,
            'imbalance': (buy_large - sell_large) / total * 100 if total > 0 else 0,
            'count': len(large)
        }
    
    def delta_by_price_levels(self, levels=10):
        """
        Дельта по ценовым уровням.
        Возвращает dict: price_level -> {buy, sell, delta}
        """
        if self.n == 0:
            return {}
        
        prices = [t['price'] for t in self.trades]
        min_p = min(prices)
        max_p = max(prices)
        
        if max_p == min_p:
            level = min_p
            buy = sum(t['size'] for t in self.trades if t['side'] == 'BUY')
            sell = sum(t['size'] for t in self.trades if t['side'] == 'SELL')
            return {level: {'buy': buy, 'sell': sell, 'delta': buy - sell}}
        
        step = (max_p - min_p) / levels
        result = {}
        
        for i in range(levels):
            low = min_p + i * step
            high = low + step
            mid = (low + high) / 2
            
            buy = sum(t['size'] for t in self.trades 
                     if t['side'] == 'BUY' and low <= t['price'] < high)
            sell = sum(t['size'] for t in self.trades 
                      if t['side'] == 'SELL' and low <= t['price'] < high)
            
            result[mid] = {'buy': buy, 'sell': sell, 'delta': buy - sell}
        
        return result
    
    def detect_divergence(self, bars, min_periods=5):
        """
        Детекция дивергенции между ценой и кумулятивной дельтой.
        
        Бычья дивергенция: цена падает, дельта растёт → сила покупателей
        Медвежья дивергенция: цена растёт, дельта падает → сила продавцов
        
        bars: list of {c} (close prices)
        Возвращает: 'BULL_DIV', 'BEAR_DIV', или None
        """
        if len(bars) < min_periods or self.n == 0:
            return None
        
        # Агрегируем сделки по барным периодам (упрощённо — по последним N барам)
        cd = self.cumulative_delta()
        if len(cd) < 2:
            return None
        
        # Цена: последние min_periods баров
        recent_closes = [b['c'] for b in bars[-min_periods:]]
        
        # Дельта: последние min_periods сегментов
        seg_size = max(1, len(cd) // min_periods)
        delta_segments = []
        for i in range(0, len(cd), seg_size):
            seg = cd[i:i + seg_size]
            if seg:
                delta_segments.append(seg[-1]['cum_delta'] - seg[0]['cum_delta'])
        
        # Сравниваем тренды
        n_compare = min(len(recent_closes), len(delta_segments))
        if n_compare < 3:
            return None
        
        price_trend = recent_closes[-1] - recent_closes[0]
        delta_trend = delta_segments[-1] - delta_segments[0] if delta_segments else 0
        
        # Дивергенция
        if price_trend < 0 and delta_trend > 0:
            return 'BULL_DIV'  # цена вниз, дельта вверх
        elif price_trend > 0 and delta_trend < 0:
            return 'BEAR_DIV'  # цена вверх, дельта вниз
        
        return None
    
    def absorption_detection(self, threshold=3.0):
        """
        Детекция абсорбции: крупная дельта в одну сторону, но цена не двигается.
        
        Возвращает list of {ts, side, delta, price_move, absorption_ratio}
        """
        if self.n < 10:
            return []
        
        absorptions = []
        window = 20  # Окно анализа
        
        for i in range(window, self.n, window // 2):
            segment = self.trades[i - window:i]
            
            buy_vol = sum(t['size'] for t in segment if t['side'] == 'BUY')
            sell_vol = sum(t['size'] for t in segment if t['side'] == 'SELL')
            delta = buy_vol - sell_vol
            
            total_vol = buy_vol + sell_vol
            if total_vol == 0:
                continue
            
            price_start = segment[0]['price']
            price_end = segment[-1]['price']
            price_move = abs(price_end - price_start) / price_start * 100 if price_start > 0 else 0
            
            # Абсорбция: сильная дельта, но цена не двигается
            delta_pct = abs(delta) / total_vol * 100
            absorption_ratio = delta_pct / max(price_move, 0.01)
            
            if absorption_ratio > threshold and delta_pct > 20:
                absorptions.append({
                    'ts': segment[-1]['ts'],
                    'side': 'BUY' if delta > 0 else 'SELL',
                    'delta': delta,
                    'delta_pct': delta_pct,
                    'price_move_pct': price_move,
                    'absorption_ratio': absorption_ratio
                })
        
        return absorptions
