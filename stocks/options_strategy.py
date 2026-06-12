import pandas as pd
import numpy as np
import logging
import datetime
import math
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator
from stocks.data_fetcher import StockDataFetcher

logger = logging.getLogger(__name__)

class BullPutSpreadStrategy:
    def __init__(self, data_fetcher: StockDataFetcher, target_dte: int = 40, target_delta: float = -0.30, 
                 spread_width: float = 5.0, min_iv_rank: float = 15.0, ema_period: int = 200):
        self.fetcher = data_fetcher
        self.target_dte = target_dte
        self.target_delta = target_delta
        self.spread_width = spread_width
        self.min_iv_rank = min_iv_rank
        self.ema_period = ema_period

    def evaluate_trend_and_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators (EMA, RSI, VWAP) on the stock history."""
        if df.empty or len(df) < self.ema_period:
            return pd.DataFrame()
            
        df_copy = df.copy()
        df_copy['ema200'] = EMAIndicator(close=df_copy['Close'], window=self.ema_period).ema_indicator()
        df_copy['ema20'] = EMAIndicator(close=df_copy['Close'], window=20).ema_indicator()
        df_copy['rsi'] = RSIIndicator(close=df_copy['Close'], window=14).rsi()
        
        # Calculate Rolling VWAP
        tp = (df_copy['High'] + df_copy['Low'] + df_copy['Close']) / 3.0
        tp_vol = tp * df_copy['Volume']
        cum_tp_vol = tp_vol.rolling(window=14).sum()
        cum_vol = df_copy['Volume'].rolling(window=14).sum()
        df_copy['vwap'] = (cum_tp_vol / cum_vol).fillna(df_copy['Close'])
        
        # 20-day high and low for momentum breakout checks
        df_copy['high_20'] = df_copy['Close'].rolling(window=20).max()
        df_copy['low_20'] = df_copy['Close'].rolling(window=20).min()
        
        # 20-day average volume
        df_copy['vol_sma20'] = df_copy['Volume'].rolling(window=20).mean()
        
        return df_copy

    def _select_opt_by_delta(self, options: pd.DataFrame, target_delta: float, option_type: str = 'put') -> pd.DataFrame:
        """Find the option contract closest to the target delta."""
        if options.empty:
            return None
        opt_copy = options.copy()
        opt_copy['delta_diff'] = abs(opt_copy['delta'] - target_delta)
        sorted_opts = opt_copy.sort_values(by='delta_diff')
        return sorted_opts.iloc[0] if not sorted_opts.empty else None

    def scan_for_spread(self, symbol: str) -> dict:
        """
        Evaluate stock indicators to select the best of the 7 options strategies:
        - Momentum Breakout Buy (Call/Put)
        - Opening Range Breakout (Call/Put)
        - Iron Condor
        - Iron Butterfly
        - Covered Call
        - Cash-Secured Put
        - Calendar Spread
        """
        logger.info(f"Scanning {symbol} for advanced options strategies...")
        
        # 1. Fetch History & Trend indicators
        hist = self.fetcher.fetch_stock_history(symbol, period="1y")
        df = self.evaluate_trend_and_indicators(hist)
        if df.empty or len(df) < self.ema_period:
            logger.warning(f"Not enough data to calculate indicators for {symbol}.")
            return {}
            
        row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else row
        
        current_close = row['Close']
        current_ema200 = row['ema200']
        current_rsi = row['rsi']
        current_vwap = row['vwap']
        current_vol = row['Volume']
        avg_vol = row['vol_sma20']
        
        # 2. Fetch Options Chain
        expiration, puts, calls, current_price = self.fetcher.fetch_option_chain(symbol, target_dte=self.target_dte)
        if (puts.empty and calls.empty) or not expiration:
            logger.info(f"No option chains found for {symbol}.")
            return {}
            
        # 3. Calculate IV Rank
        ref_df = puts if not puts.empty else calls
        atm_opts = ref_df[abs(ref_df['strike'] - current_price) / current_price <= 0.02]
        current_iv = atm_opts['impliedVolatility'].mean() if not atm_opts.empty else ref_df['impliedVolatility'].mean()
        
        iv_rank = self.fetcher.calculate_iv_rank(hist, current_iv)
        logger.info(f"{symbol} Indicators: Close=₹{current_close:.2f} | VWAP=₹{current_vwap:.2f} | RSI={current_rsi:.1f} | IV Rank={iv_rank:.1f}%")
        
        # 4. Strategy Rules Decision Matrix
        
        # SETUP A: Momentum Breakout Buy (Buying call or put ATM on price breaking key range)
        if current_close >= prev_row['high_20'] and current_rsi > 60.0 and current_close > current_vwap:
            logger.info(f"Triggered Setup: Momentum Breakout Buy (CALL) on {symbol}")
            atm_call = self._select_opt_by_delta(calls, 0.50, 'call')
            if atm_call is not None:
                net_debit = atm_call['mid']
                return {
                    'symbol': symbol,
                    'strategy_type': 'MOMENTUM_CALL_BUY',
                    'expiration': expiration,
                    'dte': int(atm_call['dte']),
                    'underlying_price': current_price,
                    'short_strike': 0.0,
                    'long_strike': atm_call['strike'],
                    'net_credit': -net_debit,  # Debit spread represented as negative credit
                    'spread_width': 0.0,
                    'max_profit': float('inf'),
                    'max_loss': net_debit,
                    'return_on_risk': 1.0,
                    'iv_rank': iv_rank,
                    'legs': [{'action': 'BUY', 'type': 'CALL', 'strike': atm_call['strike'], 'premium': net_debit}]
                }
                
        elif current_close <= prev_row['low_20'] and current_rsi < 40.0 and current_close < current_vwap:
            logger.info(f"Triggered Setup: Momentum Breakout Buy (PUT) on {symbol}")
            atm_put = self._select_opt_by_delta(puts, -0.50, 'put')
            if atm_put is not None:
                net_debit = atm_put['mid']
                return {
                    'symbol': symbol,
                    'strategy_type': 'MOMENTUM_PUT_BUY',
                    'expiration': expiration,
                    'dte': int(atm_put['dte']),
                    'underlying_price': current_price,
                    'short_strike': 0.0,
                    'long_strike': atm_put['strike'],
                    'net_credit': -net_debit,
                    'spread_width': 0.0,
                    'max_profit': float('inf'),
                    'max_loss': net_debit,
                    'return_on_risk': 1.0,
                    'iv_rank': iv_rank,
                    'legs': [{'action': 'BUY', 'type': 'PUT', 'strike': atm_put['strike'], 'premium': net_debit}]
                }
                
        # SETUP B: Opening Range Breakout (ORB) (Daily proxy: breaking previous day range with high volume)
        if current_vol > 1.3 * avg_vol:
            if current_close > prev_row['High'] * 1.005 and current_close > current_vwap:
                logger.info(f"Triggered Setup: Opening Range Breakout (CALL) on {symbol}")
                atm_call = self._select_opt_by_delta(calls, 0.50, 'call')
                if atm_call is not None:
                    net_debit = atm_call['mid']
                    return {
                        'symbol': symbol,
                        'strategy_type': 'ORB_CALL_BUY',
                        'expiration': expiration,
                        'dte': int(atm_call['dte']),
                        'underlying_price': current_price,
                        'short_strike': 0.0,
                        'long_strike': atm_call['strike'],
                        'net_credit': -net_debit,
                        'spread_width': 0.0,
                        'max_profit': float('inf'),
                        'max_loss': net_debit,
                        'return_on_risk': 1.0,
                        'iv_rank': iv_rank,
                        'legs': [{'action': 'BUY', 'type': 'CALL', 'strike': atm_call['strike'], 'premium': net_debit}]
                    }
            elif current_close < prev_row['Low'] * 0.995 and current_close < current_vwap:
                logger.info(f"Triggered Setup: Opening Range Breakout (PUT) on {symbol}")
                atm_put = self._select_opt_by_delta(puts, -0.50, 'put')
                if atm_put is not None:
                    net_debit = atm_put['mid']
                    return {
                        'symbol': symbol,
                        'strategy_type': 'ORB_PUT_BUY',
                        'expiration': expiration,
                        'dte': int(atm_put['dte']),
                        'underlying_price': current_price,
                        'short_strike': 0.0,
                        'long_strike': atm_put['strike'],
                        'net_credit': -net_debit,
                        'spread_width': 0.0,
                        'max_profit': float('inf'),
                        'max_loss': net_debit,
                        'return_on_risk': 1.0,
                        'iv_rank': iv_rank,
                        'legs': [{'action': 'BUY', 'type': 'PUT', 'strike': atm_put['strike'], 'premium': net_debit}]
                    }
                    
        # Filter other option strategies by min_iv_rank
        if iv_rank < self.min_iv_rank:
            logger.info(f"IV Rank {iv_rank:.1f}% below minimum threshold {self.min_iv_rank}%. Skipping option selling spreads.")
            return {}

        # Determine general regime for non-breakout strategies
        if current_close > current_ema200 * 1.015:
            regime = 'BULLISH'
        elif current_close < current_ema200 * 0.985:
            regime = 'BEARISH'
        else:
            regime = 'NEUTRAL'

        # SETUP C: Iron Condor or Iron Butterfly (Neutral Range-Bound with High Volatility)
        if regime == 'NEUTRAL' and iv_rank >= 30.0:
            if iv_rank >= 50.0:
                # Iron Butterfly: Sell ATM Put & Call, Buy OTM Put & Call
                logger.info(f"Triggered Setup: Iron Butterfly on {symbol}")
                # Find ATM strike
                atm_strike = min(puts['strike'], key=lambda k: abs(k - current_price))
                short_put = puts[puts['strike'] == atm_strike].iloc[0]
                short_call = calls[calls['strike'] == atm_strike].iloc[0]
                
                # Wings
                long_put_strike = atm_strike - self.spread_width
                long_call_strike = atm_strike + self.spread_width
                
                long_put = puts[puts['strike'] == long_put_strike]
                long_call = calls[calls['strike'] == long_call_strike]
                
                if not long_put.empty and not long_call.empty:
                    lp = long_put.iloc[0]
                    lc = long_call.iloc[0]
                    net_credit = (short_put['mid'] + short_call['mid']) - (lp['mid'] + lc['mid'])
                    if net_credit > 0:
                        max_loss = self.spread_width - net_credit
                        return {
                            'symbol': symbol,
                            'strategy_type': 'IRON_BUTTERFLY',
                            'expiration': expiration,
                            'dte': int(short_put['dte']),
                            'underlying_price': current_price,
                            'short_strike': atm_strike,
                            'long_strike': long_put_strike,
                            'short_put_strike': atm_strike,
                            'long_put_strike': long_put_strike,
                            'short_call_strike': atm_strike,
                            'long_call_strike': long_call_strike,
                            'net_credit': net_credit,
                            'spread_width': self.spread_width,
                            'max_profit': net_credit,
                            'max_loss': max_loss if max_loss > 0 else 0.05,
                            'return_on_risk': net_credit / max_loss if max_loss > 0 else 0,
                            'iv_rank': iv_rank,
                            'legs': [
                                {'action': 'SELL', 'type': 'PUT', 'strike': atm_strike},
                                {'action': 'SELL', 'type': 'CALL', 'strike': atm_strike},
                                {'action': 'BUY', 'type': 'PUT', 'strike': long_put_strike},
                                {'action': 'BUY', 'type': 'CALL', 'strike': long_call_strike}
                            ]
                        }
            else:
                # Iron Condor: Sell OTM Put/Call spreads
                logger.info(f"Triggered Setup: Iron Condor on {symbol}")
                sorted_puts = puts[puts['strike'] < current_price].copy()
                sorted_puts['delta_diff'] = abs(sorted_puts['delta'] - self.target_delta)
                sorted_puts = sorted_puts.sort_values('delta_diff')
                
                sorted_calls = calls[calls['strike'] > current_price].copy()
                sorted_calls['delta_diff'] = abs(sorted_calls['delta'] - abs(self.target_delta))
                sorted_calls = sorted_calls.sort_values('delta_diff')
                
                if not sorted_puts.empty and not sorted_calls.empty:
                    sp = sorted_puts.iloc[0]
                    sc = sorted_calls.iloc[0]
                    
                    lp_strike = sp['strike'] - self.spread_width
                    lc_strike = sc['strike'] + self.spread_width
                    
                    long_put = puts[puts['strike'] == lp_strike]
                    long_call = calls[calls['strike'] == lc_strike]
                    
                    if not long_put.empty and not long_call.empty:
                        lp = long_put.iloc[0]
                        lc = long_call.iloc[0]
                        net_credit = (sp['mid'] - lp['mid']) + (sc['mid'] - lc['mid'])
                        if net_credit > 0:
                            max_loss = self.spread_width - net_credit
                            return {
                                'symbol': symbol,
                                'strategy_type': 'IRON_CONDOR',
                                'expiration': expiration,
                                'dte': int(sp['dte']),
                                'underlying_price': current_price,
                                'short_strike': sp['strike'],
                                'long_strike': lp_strike,
                                'short_put_strike': sp['strike'],
                                'long_put_strike': lp_strike,
                                'short_call_strike': sc['strike'],
                                'long_call_strike': lc_strike,
                                'net_credit': net_credit,
                                'spread_width': self.spread_width,
                                'max_profit': net_credit,
                                'max_loss': max_loss if max_loss > 0 else 0.05,
                                'return_on_risk': net_credit / max_loss if max_loss > 0 else 0,
                                'iv_rank': iv_rank,
                                'legs': [
                                    {'action': 'SELL', 'type': 'PUT', 'strike': sp['strike']},
                                    {'action': 'SELL', 'type': 'CALL', 'strike': sc['strike']},
                                    {'action': 'BUY', 'type': 'PUT', 'strike': lp_strike},
                                    {'action': 'BUY', 'type': 'CALL', 'strike': lc_strike}
                                ]
                            }
                            
        # SETUP D: Calendar Spread (Neutral/Low IV regime - expecting Vol expansion)
        if regime == 'NEUTRAL' and iv_rank < 15.0:
            logger.info(f"Triggered Setup: Calendar Spread (ATM Call) on {symbol}")
            # Find ATM strike
            atm_strike = min(calls['strike'], key=lambda k: abs(k - current_price))
            near_atm_call = calls[calls['strike'] == atm_strike].iloc[0]
            
            # Fetch next month expiration chain (synthetic/simulated next expiry has DTE around target_dte + 30)
            next_month_expiration, _, next_calls, _ = self.fetcher.fetch_option_chain(symbol, target_dte=self.target_dte + 30)
            if not next_calls.empty:
                far_atm_call = next_calls[next_calls['strike'] == atm_strike]
                if not far_atm_call.empty:
                    fac = far_atm_call.iloc[0]
                    net_debit = fac['mid'] - near_atm_call['mid']
                    if net_debit > 0:
                        return {
                            'symbol': symbol,
                            'strategy_type': 'CALENDAR_SPREAD',
                            'expiration': f"{expiration}/{next_month_expiration}",
                            'dte': int(near_atm_call['dte']),
                            'underlying_price': current_price,
                            'short_strike': atm_strike,
                            'long_strike': atm_strike,
                            'net_credit': -net_debit, # debit spread
                            'spread_width': 0.0,
                            'max_profit': fac['mid'] * 0.8, # Estimated
                            'max_loss': net_debit,
                            'return_on_risk': 0.8,
                            'iv_rank': iv_rank,
                            'legs': [
                                {'action': 'SELL', 'type': 'CALL', 'strike': atm_strike, 'expiry': expiration},
                                {'action': 'BUY', 'type': 'CALL', 'strike': atm_strike, 'expiry': next_month_expiration}
                            ]
                        }

        # SETUP E: Cash-Secured Put (Bullish Consolidation / Near Support)
        if regime == 'BULLISH' and current_rsi < 55.0 and current_close > current_vwap:
            logger.info(f"Triggered Setup: Cash-Secured Put on {symbol}")
            # Sell 1 OTM Put contract close to -0.30 delta
            sorted_puts = puts[puts['strike'] < current_price].copy()
            sorted_puts['delta_diff'] = abs(sorted_puts['delta'] - self.target_delta)
            sorted_puts = sorted_puts.sort_values('delta_diff')
            if not sorted_puts.empty:
                sp = sorted_puts.iloc[0]
                net_credit = sp['mid']
                max_loss = sp['strike'] - net_credit
                return {
                    'symbol': symbol,
                    'strategy_type': 'CASH_SECURED_PUT',
                    'expiration': expiration,
                    'dte': int(sp['dte']),
                    'underlying_price': current_price,
                    'short_strike': sp['strike'],
                    'long_strike': 0.0,
                    'net_credit': net_credit,
                    'spread_width': sp['strike'], # Security required is strike price
                    'max_profit': net_credit,
                    'max_loss': max_loss if max_loss > 0 else sp['strike'],
                    'return_on_risk': net_credit / max_loss if max_loss > 0 else 0,
                    'iv_rank': iv_rank,
                    'legs': [{'action': 'SELL', 'type': 'PUT', 'strike': sp['strike']}]
                }

        # SETUP F: Covered Call (Moderate Uptrend, stable/low volatility)
        if regime == 'BULLISH' and iv_rank < 30.0:
            logger.info(f"Triggered Setup: Covered Call on {symbol}")
            # Buy Stock + Sell OTM Call (~0.30 delta)
            sorted_calls = calls[calls['strike'] > current_price].copy()
            sorted_calls['delta_diff'] = abs(sorted_calls['delta'] - abs(self.target_delta))
            sorted_calls = sorted_calls.sort_values('delta_diff')
            if not sorted_calls.empty:
                sc = sorted_calls.iloc[0]
                net_credit = sc['mid']
                # Cost to enter Covered Call = stock_price - call_premium
                entry_cost = current_price - net_credit
                return {
                    'symbol': symbol,
                    'strategy_type': 'COVERED_CALL',
                    'expiration': expiration,
                    'dte': int(sc['dte']),
                    'underlying_price': current_price,
                    'short_strike': sc['strike'],
                    'long_strike': 0.0,
                    'net_credit': net_credit,
                    'spread_width': entry_cost,  # Capital required is S - Call
                    'max_profit': (sc['strike'] - current_price) + net_credit,
                    'max_loss': entry_cost,
                    'return_on_risk': ((sc['strike'] - current_price) + net_credit) / entry_cost if entry_cost > 0 else 0,
                    'iv_rank': iv_rank,
                    'legs': [
                        {'action': 'BUY', 'type': 'STOCK', 'strike': current_price},
                        {'action': 'SELL', 'type': 'CALL', 'strike': sc['strike']}
                    ]
                }

        # Fallback default: Bull Put Spread if BULLISH
        if regime == 'BULLISH':
            logger.info(f"Fallback Setup: Bull Put Spread on {symbol}")
            # Find Put strikes
            sorted_puts = puts[puts['strike'] < current_price].copy()
            sorted_puts['delta_diff'] = abs(sorted_puts['delta'] - self.target_delta)
            sorted_puts = sorted_puts.sort_values('delta_diff')
            if not sorted_puts.empty:
                sp = sorted_puts.iloc[0]
                target_long = sp['strike'] - self.spread_width
                lp_df = puts[puts['strike'] == target_long]
                if not lp_df.empty:
                    lp = lp_df.iloc[0]
                    net_credit = sp['mid'] - lp['mid']
                    if net_credit > 0:
                        max_loss = self.spread_width - net_credit
                        return {
                            'symbol': symbol,
                            'strategy_type': 'BULL_PUT',
                            'expiration': expiration,
                            'dte': int(sp['dte']),
                            'underlying_price': current_price,
                            'short_strike': sp['strike'],
                            'long_strike': target_long,
                            'net_credit': net_credit,
                            'spread_width': self.spread_width,
                            'max_profit': net_credit,
                            'max_loss': max_loss if max_loss > 0 else 0.05,
                            'return_on_risk': net_credit / max_loss if max_loss > 0 else 0,
                            'iv_rank': iv_rank,
                            'legs': [
                                {'action': 'SELL', 'type': 'PUT', 'strike': sp['strike']},
                                {'action': 'BUY', 'type': 'PUT', 'strike': target_long}
                            ]
                        }

        # Fallback default: Bear Call Spread if BEARISH
        if regime == 'BEARISH':
            logger.info(f"Fallback Setup: Bear Call Spread on {symbol}")
            sorted_calls = calls[calls['strike'] > current_price].copy()
            sorted_calls['delta_diff'] = abs(sorted_calls['delta'] - abs(self.target_delta))
            sorted_calls = sorted_calls.sort_values('delta_diff')
            if not sorted_calls.empty:
                sc = sorted_calls.iloc[0]
                target_long = sc['strike'] + self.spread_width
                lc_df = calls[calls['strike'] == target_long]
                if not lc_df.empty:
                    lc = lc_df.iloc[0]
                    net_credit = sc['mid'] - lc['mid']
                    if net_credit > 0:
                        max_loss = self.spread_width - net_credit
                        return {
                            'symbol': symbol,
                            'strategy_type': 'BEAR_CALL',
                            'expiration': expiration,
                            'dte': int(sc['dte']),
                            'underlying_price': current_price,
                            'short_strike': sc['strike'],
                            'long_strike': target_long,
                            'net_credit': net_credit,
                            'spread_width': self.spread_width,
                            'max_profit': net_credit,
                            'max_loss': max_loss if max_loss > 0 else 0.05,
                            'return_on_risk': net_credit / max_loss if max_loss > 0 else 0,
                            'iv_rank': iv_rank,
                            'legs': [
                                {'action': 'SELL', 'type': 'CALL', 'strike': sc['strike']},
                                {'action': 'BUY', 'type': 'CALL', 'strike': target_long}
                            ]
                        }

        return {}

def format_strategy_legs(spread_info: dict, indent: str = "") -> str:
    strat = spread_info.get('strategy_type', 'BULL_PUT')
    
    if strat in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
        return f"{indent}Buy Long Call: ₹{spread_info.get('long_strike', 0.0):.2f}"
    elif strat in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
        return f"{indent}Buy Long Put: ₹{spread_info.get('long_strike', 0.0):.2f}"
    elif strat == 'CASH_SECURED_PUT':
        return f"{indent}Sell Short Put: ₹{spread_info.get('short_strike', 0.0):.2f}"
    elif strat == 'COVERED_CALL':
        return f"{indent}Buy Underlying Stock + Sell Short Call: ₹{spread_info.get('short_strike', 0.0):.2f}"
    elif strat == 'CALENDAR_SPREAD':
        return f"{indent}Calendar Spread (ATM Call): ₹{spread_info.get('short_strike', 0.0):.2f}"
    elif strat == 'BULL_PUT':
        return f"{indent}Sell ₹{spread_info.get('short_strike', 0.0):.2f} Put / Buy ₹{spread_info.get('long_strike', 0.0):.2f} Put"
    elif strat == 'BEAR_CALL':
        return f"{indent}Sell ₹{spread_info.get('short_strike', 0.0):.2f} Call / Buy ₹{spread_info.get('long_strike', 0.0):.2f} Call"
    elif strat in ('IRON_CONDOR', 'IRON_BUTTERFLY'):
        return (
            f"{indent}Put: Sell ₹{spread_info.get('short_put_strike', 0.0):.2f} / Buy ₹{spread_info.get('long_put_strike', 0.0):.2f}\n"
            f"{indent}Call: Sell ₹{spread_info.get('short_call_strike', 0.0):.2f} / Buy ₹{spread_info.get('long_call_strike', 0.0):.2f}"
        )
    else:
        return f"{indent}Strikes: Short {spread_info.get('short_strike', 0.0):.2f} / Long {spread_info.get('long_strike', 0.0):.2f}"


