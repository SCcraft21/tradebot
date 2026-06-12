import pandas as pd
import logging
from ta.trend import EMAIndicator
from stocks.data_fetcher import StockDataFetcher

logger = logging.getLogger(__name__)

class BullPutSpreadStrategy:
    def __init__(self, data_fetcher: StockDataFetcher, target_dte: int = 40, target_delta: float = -0.30, 
                 spread_width: float = 5.0, min_iv_rank: float = 30.0, ema_period: int = 200):
        self.fetcher = data_fetcher
        self.target_dte = target_dte
        self.target_delta = target_delta
        self.spread_width = spread_width
        self.min_iv_rank = min_iv_rank
        self.ema_period = ema_period

    def evaluate_trend(self, df: pd.DataFrame) -> bool:
        """Verify the stock is in an uptrend (Close > EMA 200)."""
        if df.empty or len(df) < self.ema_period:
            logger.warning("Not enough data to calculate trend.")
            return False
            
        df_copy = df.copy()
        df_copy['ema200'] = EMAIndicator(close=df_copy['Close'], window=self.ema_period).ema_indicator()
        
        current_close = df_copy['Close'].iloc[-1]
        current_ema200 = df_copy['ema200'].iloc[-1]
        
        is_bullish = current_close > current_ema200
        logger.info(f"Trend Check: Close=₹{current_close:.2f} | EMA200=₹{current_ema200:.2f} | Bullish: {is_bullish}")
        return is_bullish

    def _select_put_spread(self, puts: pd.DataFrame, current_price: float):
        if puts.empty:
            return None
        puts_copy = puts.copy()
        puts_copy['delta_diff'] = abs(puts_copy['delta'] - self.target_delta)
        sorted_puts = puts_copy.sort_values(by='delta_diff')
        
        short_put_candidates = sorted_puts[sorted_puts['strike'] < current_price]
        if short_put_candidates.empty:
            return None
        short_put = short_put_candidates.iloc[0]
        short_strike = short_put['strike']
        
        target_long_strike = short_strike - self.spread_width
        puts_copy['long_strike_diff'] = abs(puts_copy['strike'] - target_long_strike)
        long_put_candidates = puts_copy[puts_copy['strike'] < short_strike].sort_values(by='long_strike_diff')
        if long_put_candidates.empty:
            return None
        long_put = long_put_candidates.iloc[0]
        return short_put, long_put

    def _select_call_spread(self, calls: pd.DataFrame, current_price: float):
        if calls.empty:
            return None
        calls_copy = calls.copy()
        # Calls delta is positive, so we scan closest to abs(target_delta)
        calls_copy['delta_diff'] = abs(calls_copy['delta'] - abs(self.target_delta))
        sorted_calls = calls_copy.sort_values(by='delta_diff')
        
        short_call_candidates = sorted_calls[sorted_calls['strike'] > current_price]
        if short_call_candidates.empty:
            return None
        short_call = short_call_candidates.iloc[0]
        short_strike = short_call['strike']
        
        target_long_strike = short_strike + self.spread_width
        calls_copy['long_strike_diff'] = abs(calls_copy['strike'] - target_long_strike)
        long_call_candidates = calls_copy[calls_copy['strike'] > short_strike].sort_values(by='long_strike_diff')
        if long_call_candidates.empty:
            return None
        long_call = long_call_candidates.iloc[0]
        return short_call, long_call

    def scan_for_spread(self, symbol: str) -> dict:
        """
        Evaluate stock and options chain to find a Bull Put Spread, Bear Call Spread, or Iron Condor.
        Returns a dict with spread parameters if found, else empty dict.
        """
        logger.info(f"Scanning {symbol} for options spread opportunities...")
        
        # 1. Fetch History & Trend check
        hist = self.fetcher.fetch_stock_history(symbol, period="1y")
        if hist.empty or len(hist) < self.ema_period:
            logger.warning(f"Not enough data to calculate trend for {symbol}.")
            return {}
            
        df_copy = hist.copy()
        df_copy['ema200'] = EMAIndicator(close=df_copy['Close'], window=self.ema_period).ema_indicator()
        current_close = df_copy['Close'].iloc[-1]
        current_ema200 = df_copy['ema200'].iloc[-1]
        
        # Determine regime
        if current_close > current_ema200 * 1.01:
            regime = 'BULLISH'
        elif current_close < current_ema200 * 0.99:
            regime = 'BEARISH'
        else:
            regime = 'NEUTRAL'
            
        logger.info(f"Regime Check for {symbol}: Close={current_close:.2f} | EMA200={current_ema200:.2f} | Regime={regime}")
        
        # 2. Fetch Options Chain
        expiration, puts, calls, current_price = self.fetcher.fetch_option_chain(symbol, target_dte=self.target_dte)
        if (puts.empty and calls.empty) or not expiration:
            logger.info(f"No option chains found for {symbol}.")
            return {}
            
        # 3. IV Rank Check
        ref_df = puts if not puts.empty else calls
        atm_opts = ref_df[abs(ref_df['strike'] - current_price) / current_price <= 0.02]
        current_iv = atm_opts['impliedVolatility'].mean() if not atm_opts.empty else ref_df['impliedVolatility'].mean()
        
        iv_rank = self.fetcher.calculate_iv_rank(hist, current_iv)
        logger.info(f"Volatility check: Estimated Current IV={current_iv*100:.2f}% | IV Rank={iv_rank:.2f}%")
        
        if iv_rank < self.min_iv_rank:
            logger.info(f"IV Rank {iv_rank:.2f}% is below threshold {self.min_iv_rank}%. Skipping {symbol}.")
            return {}
            
        # 4. Strategy Selection
        if regime == 'BULLISH':
            put_spread = self._select_put_spread(puts, current_price)
            if not put_spread:
                logger.info(f"No valid Put Spread found for {symbol}.")
                return {}
            short_put, long_put = put_spread
            net_credit = short_put['mid'] - long_put['mid']
            
            if net_credit <= 0:
                logger.warning("Calculated negative net credit for Put Spread.")
                return {}
                
            actual_width = short_put['strike'] - long_put['strike']
            max_profit = net_credit
            max_loss = actual_width - net_credit
            
            spread_info = {
                'symbol': symbol,
                'strategy_type': 'BULL_PUT',
                'expiration': expiration,
                'dte': int(short_put['dte']),
                'underlying_price': current_price,
                'short_strike': short_put['strike'],
                'short_delta': short_put['delta'],
                'short_iv': short_put['impliedVolatility'],
                'long_strike': long_put['strike'],
                'long_delta': long_put['delta'],
                'long_iv': long_put['impliedVolatility'],
                'short_mid': short_put['mid'],
                'long_mid': long_put['mid'],
                'net_credit': net_credit,
                'spread_width': actual_width,
                'max_profit': max_profit,
                'max_loss': max_loss,
                'return_on_risk': max_profit / max_loss if max_loss > 0 else 0,
                'iv_rank': iv_rank
            }
            logger.info(f"[BULL_PUT] Found Spread for {symbol}: Sell ₹{short_put['strike']:.2f} Put / Buy ₹{long_put['strike']:.2f} Put | Net Credit: ₹{net_credit:.2f}")
            return spread_info
            
        elif regime == 'BEARISH':
            call_spread = self._select_call_spread(calls, current_price)
            if not call_spread:
                logger.info(f"No valid Call Spread found for {symbol}.")
                return {}
            short_call, long_call = call_spread
            net_credit = short_call['mid'] - long_call['mid']
            
            if net_credit <= 0:
                logger.warning("Calculated negative net credit for Call Spread.")
                return {}
                
            actual_width = long_call['strike'] - short_call['strike']
            max_profit = net_credit
            max_loss = actual_width - net_credit
            
            spread_info = {
                'symbol': symbol,
                'strategy_type': 'BEAR_CALL',
                'expiration': expiration,
                'dte': int(short_call['dte']),
                'underlying_price': current_price,
                'short_strike': short_call['strike'],
                'short_delta': short_call['delta'],
                'short_iv': short_call['impliedVolatility'],
                'long_strike': long_call['strike'],
                'long_delta': long_call['delta'],
                'long_iv': long_call['impliedVolatility'],
                'short_mid': short_call['mid'],
                'long_mid': long_call['mid'],
                'net_credit': net_credit,
                'spread_width': actual_width,
                'max_profit': max_profit,
                'max_loss': max_loss,
                'return_on_risk': max_profit / max_loss if max_loss > 0 else 0,
                'iv_rank': iv_rank
            }
            logger.info(f"[BEAR_CALL] Found Spread for {symbol}: Sell ₹{short_call['strike']:.2f} Call / Buy ₹{long_call['strike']:.2f} Call | Net Credit: ₹{net_credit:.2f}")
            return spread_info
            
        else:  # NEUTRAL -> IRON_CONDOR
            put_spread = self._select_put_spread(puts, current_price)
            call_spread = self._select_call_spread(calls, current_price)
            if not put_spread or not call_spread:
                logger.info(f"Could not construct both sides for Iron Condor on {symbol}.")
                return {}
                
            short_put, long_put = put_spread
            short_call, long_call = call_spread
            
            put_credit = short_put['mid'] - long_put['mid']
            call_credit = short_call['mid'] - long_call['mid']
            net_credit = put_credit + call_credit
            
            if put_credit <= 0 or call_credit <= 0:
                logger.warning("Negative net credit in one of the Iron Condor wings.")
                return {}
                
            actual_width = short_put['strike'] - long_put['strike']
            max_profit = net_credit
            max_loss = actual_width - net_credit
            
            spread_info = {
                'symbol': symbol,
                'strategy_type': 'IRON_CONDOR',
                'expiration': expiration,
                'dte': int(short_put['dte']),
                'underlying_price': current_price,
                # Put side strikes
                'short_strike': short_put['strike'], # Backwards compatibility for single side logic
                'long_strike': long_put['strike'],
                'short_put_strike': short_put['strike'],
                'long_put_strike': long_put['strike'],
                'short_put_delta': short_put['delta'],
                'short_put_mid': short_put['mid'],
                'long_put_mid': long_put['mid'],
                # Call side strikes
                'short_call_strike': short_call['strike'],
                'long_call_strike': long_call['strike'],
                'short_call_delta': short_call['delta'],
                'short_call_mid': short_call['mid'],
                'long_call_mid': long_call['mid'],
                # Pricing & Risk
                'net_credit': net_credit,
                'spread_width': actual_width,
                'max_profit': max_profit,
                'max_loss': max_loss,
                'return_on_risk': max_profit / max_loss if max_loss > 0 else 0,
                'iv_rank': iv_rank
            }
            logger.info(f"[IRON_CONDOR] Found Spread for {symbol}: Put ₹{long_put['strike']:.2f}/₹{short_put['strike']:.2f} | Call ₹{short_call['strike']:.2f}/₹{long_call['strike']:.2f} | Total Credit: ₹{net_credit:.2f}")
            return spread_info
