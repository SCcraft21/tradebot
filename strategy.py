import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange
import logging

logger = logging.getLogger(__name__)

class SwingStrategy:
    def __init__(self, rsi_period: int, rsi_oversold: float, ema_period: int, ema_proximity_pct: float,
                 ema_long_period: int = 200, atr_period: int = 14,
                 require_volume_spike: bool = True, require_macd: bool = True, require_ema200: bool = True):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.ema_period = ema_period
        self.ema_proximity_pct = ema_proximity_pct
        self.ema_long_period = ema_long_period
        self.atr_period = atr_period
        self.require_volume_spike = require_volume_spike
        self.require_macd = require_macd
        self.require_ema200 = require_ema200

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply confluence logic: RSI < 35, near EMA(50) support, volume spike, above EMA(200) trend, MACD crossover."""
        min_required = max(self.ema_long_period, self.ema_period, 26 + 9)
        if df.empty or len(df) < min_required:
            return df
        
        # Calculate Indicators
        df['rsi'] = RSIIndicator(close=df['close'], window=self.rsi_period).rsi()
        df['ema50'] = EMAIndicator(close=df['close'], window=self.ema_period).ema_indicator()
        df['ema200'] = EMAIndicator(close=df['close'], window=self.ema_long_period).ema_indicator()
        
        # MACD
        macd_indicator = MACD(close=df['close'], window_slow=26, window_fast=12, window_sign=9)
        df['macd'] = macd_indicator.macd()
        df['macd_signal'] = macd_indicator.macd_signal()
        df['macd_diff'] = macd_indicator.macd_diff()
        
        # ATR
        df['atr'] = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=self.atr_period).average_true_range()
        
        # Volume Spike (Volume > 1.5 * 20-period SMA volume)
        df['volume_sma20'] = df['volume'].rolling(window=20).mean()
        df['volume_spike'] = df['volume'] > (1.5 * df['volume_sma20'])

        # Signal Logic
        # 1. RSI oversold
        rsi_condition = df['rsi'] < self.rsi_oversold
        
        # 2. Price near EMA50 (within proximity percentage)
        price_near_ema = abs(df['close'] - df['ema50']) / df['ema50'] <= self.ema_proximity_pct
        price_above_ema = df['close'] >= df['ema50'] * (1 - self.ema_proximity_pct)
        
        buy_signal = rsi_condition & price_near_ema & price_above_ema
        
        # 3. Long-term trend confirmation: price is above EMA 200
        if self.require_ema200:
            buy_signal = buy_signal & (df['close'] > df['ema200'])
        
        # 4. Momentum confirmation: MACD line > Signal line (bullish zone or crossover)
        if self.require_macd:
            buy_signal = buy_signal & (df['macd'] > df['macd_signal'])
            
        # 5. Volume Spike
        if self.require_volume_spike:
            buy_signal = buy_signal & df['volume_spike']

        df['buy_signal'] = buy_signal
        
        return df

    def get_latest_signal(self, df: pd.DataFrame) -> bool:
        """Returns True if the very last closed candle fired a buy signal."""
        df_signals = self.generate_signals(df)
        if df_signals.empty or 'buy_signal' not in df_signals.columns:
            return False
        return bool(df_signals.iloc[-1]['buy_signal'])

