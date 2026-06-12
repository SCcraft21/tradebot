import pandas as pd
import numpy as np
import math
import logging
from ta.trend import EMAIndicator

logger = logging.getLogger(__name__)

class StocksOptionsBacktester:
    def __init__(self, target_dte: int = 30, target_delta: float = -0.25, spread_width: float = 100.0, 
                 min_iv_rank: float = 15.0, profit_target_pct: float = 0.40, stop_loss_mult: float = 2.0,
                 risk_free_rate: float = 0.045, initial_capital: float = 1000000.0, max_capital_per_spread_pct: float = 0.40,
                 lot_size: float = 100.0):
        self.target_dte = target_dte
        self.target_delta = target_delta
        self.spread_width = spread_width
        self.min_iv_rank = min_iv_rank
        self.profit_target_pct = profit_target_pct
        self.stop_loss_mult = stop_loss_mult
        self.risk_free_rate = risk_free_rate
        self.initial_capital = initial_capital
        self.max_capital_per_spread_pct = max_capital_per_spread_pct
        self.lot_size = lot_size

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _bs_put_price(self, S: float, K: float, T: float, sigma: float) -> float:
        """Calculate Black-Scholes Put Option Price."""
        if T <= 0:
            return max(0.0, K - S)
        if sigma <= 0:
            sigma = 0.01
            
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        put_price = K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(-d2) - S * self._norm_cdf(-d1)
        return max(0.0, put_price)

    def _bs_call_price(self, S: float, K: float, T: float, sigma: float) -> float:
        """Calculate Black-Scholes Call Option Price."""
        if T <= 0:
            return max(0.0, S - K)
        if sigma <= 0:
            sigma = 0.01
            
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        call_price = S * self._norm_cdf(d1) - K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(d2)
        return max(0.0, call_price)

    def _calculate_option_delta(self, S: float, K: float, T: float, sigma: float, option_type: str = 'put') -> float:
        """Calculate the option Delta for a Put or Call."""
        if T <= 0:
            if option_type == 'put':
                return -1.0 if S < K else 0.0
            else:
                return 1.0 if S > K else 0.0
                
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        put_delta = self._norm_cdf(d1) - 1.0
        
        if option_type == 'put':
            return put_delta
        else:
            return put_delta + 1.0  # Call Delta = Put Delta + 1

    def run_backtest(self, historical_data: dict) -> dict:
        """
        Run options backtest over daily historical bar data.
        historical_data: Dict[symbol, DataFrame]
        """
        logger.info("Starting stock options backtest simulation...")
        capital = self.initial_capital
        trades = []
        
        for symbol, df in historical_data.items():
            if df.empty or len(df) < 252:
                logger.warning(f"Insufficient data for {symbol} (requires at least 252 bars).")
                continue
                
            df = df.copy()
            
            # Calculate EMA 200
            df['ema200'] = EMAIndicator(close=df['Close'], window=200).ema_indicator()
            
            # Calculate rolling 20-day Historical Volatility
            log_returns = np.log(df['Close'] / df['Close'].shift(1))
            df['hv'] = log_returns.rolling(window=20).std() * math.sqrt(252)
            
            # Max/Min HV for IV Rank
            df['min_hv'] = df['hv'].rolling(window=252).min()
            df['max_hv'] = df['hv'].rolling(window=252).max()
            
            df = df.dropna()
            
            in_position = False
            position = {}
            
            # Step through time daily
            for index, row in df.iterrows():
                S_t = row['Close']
                hv_t = row['hv']
                
                # Calculate estimated IV Rank
                min_hv = row['min_hv']
                max_hv = row['max_hv']
                iv_rank = ((hv_t - min_hv) / (max_hv - min_hv)) * 100.0 if max_hv != min_hv else 50.0
                
                # 1. Manage Active Position
                if in_position:
                    position['current_dte'] -= 1
                    T_t = position['current_dte'] / 365.0
                    strat = position['strategy_type']
                    
                    # Calculate current spread value (buyback value)
                    if strat == 'BULL_PUT':
                        short_price = self._bs_put_price(S_t, position['short_strike'], T_t, hv_t)
                        long_price = self._bs_put_price(S_t, position['long_strike'], T_t, hv_t)
                        current_spread_val = short_price - long_price
                    elif strat == 'BEAR_CALL':
                        short_price = self._bs_call_price(S_t, position['short_strike'], T_t, hv_t)
                        long_price = self._bs_call_price(S_t, position['long_strike'], T_t, hv_t)
                        current_spread_val = short_price - long_price
                    else:  # IRON_CONDOR
                        short_put_p = self._bs_put_price(S_t, position['short_put_strike'], T_t, hv_t)
                        long_put_p = self._bs_put_price(S_t, position['long_put_strike'], T_t, hv_t)
                        short_call_p = self._bs_call_price(S_t, position['short_call_strike'], T_t, hv_t)
                        long_call_p = self._bs_call_price(S_t, position['long_call_strike'], T_t, hv_t)
                        current_spread_val = (short_put_p - long_put_p) + (short_call_p - long_call_p)
                    
                    # Exit criteria
                    profit_target = position['net_credit'] * (1 - self.profit_target_pct)
                    stop_loss = position['net_credit'] * self.stop_loss_mult
                    
                    pnl = 0.0
                    closed = False
                    reason = ""
                    
                    if current_spread_val <= profit_target:
                        pnl = (position['net_credit'] - current_spread_val) * position['contracts'] * self.lot_size
                        closed = True
                        reason = "PROFIT_TARGET"
                    elif current_spread_val >= stop_loss:
                        pnl = (position['net_credit'] - current_spread_val) * position['contracts'] * self.lot_size
                        closed = True
                        reason = "STOP_LOSS"
                    elif position['current_dte'] <= 0:
                        # Expiration settlement
                        if strat == 'BULL_PUT':
                            if S_t >= position['short_strike']:
                                settlement_value = 0.0
                            elif S_t <= position['long_strike']:
                                settlement_value = position['spread_width']
                            else:
                                settlement_value = position['short_strike'] - S_t
                        elif strat == 'BEAR_CALL':
                            if S_t <= position['short_strike']:
                                settlement_value = 0.0
                            elif S_t >= position['long_strike']:
                                settlement_value = position['spread_width']
                            else:
                                settlement_value = S_t - position['short_strike']
                        else:  # IRON_CONDOR
                            # Put wing settlement
                            if S_t <= position['long_put_strike']:
                                put_loss = position['short_put_strike'] - position['long_put_strike']
                            elif S_t < position['short_put_strike']:
                                put_loss = position['short_put_strike'] - S_t
                            else:
                                put_loss = 0.0
                            # Call wing settlement
                            if S_t >= position['long_call_strike']:
                                call_loss = position['long_call_strike'] - position['short_call_strike']
                            elif S_t > position['short_call_strike']:
                                call_loss = S_t - position['short_call_strike']
                            else:
                                call_loss = 0.0
                            settlement_value = put_loss + call_loss
                            
                        pnl = (position['net_credit'] - settlement_value) * position['contracts'] * self.lot_size
                        closed = True
                        reason = "EXPIRATION"
                        
                    if closed:
                        capital += pnl
                        trades.append({
                            'symbol': symbol,
                            'strategy_type': strat,
                            'entry_date': position['entry_date'].strftime("%Y-%m-%d"),
                            'exit_date': index.strftime("%Y-%m-%d"),
                            'reason': reason,
                            'pnl': pnl,
                            'net_credit': position['net_credit']
                        })
                        in_position = False
                        
                # 2. Enter New Position
                if not in_position:
                    # Determine regime
                    ema = row['ema200']
                    if S_t > ema * 1.01:
                        regime = 'BULLISH'
                    elif S_t < ema * 0.99:
                        regime = 'BEARISH'
                    else:
                        regime = 'NEUTRAL'
                        
                    iv_rank_high = iv_rank >= self.min_iv_rank
                    
                    if iv_rank_high:
                        T_initial = self.target_dte / 365.0
                        
                        # Step size
                        if S_t > 15000: step = 100.0
                        elif S_t > 5000: step = 50.0
                        elif S_t > 1000: step = 20.0
                        elif S_t > 500: step = 10.0
                        elif S_t > 100: step = 5.0
                        else: step = 1.0
                        
                        # Find Put strikes if BULLISH or NEUTRAL
                        put_spread = None
                        if regime in ['BULLISH', 'NEUTRAL']:
                            best_short_put = None
                            min_p_diff = 9999
                            min_k = math.floor((S_t * 0.8) / step) * step
                            max_k = math.ceil((S_t * 0.99) / step) * step
                            for k_cand in np.arange(min_k, max_k + step, step):
                                d_put = self._calculate_option_delta(S_t, k_cand, T_initial, hv_t, 'put')
                                diff = abs(d_put - self.target_delta)
                                if diff < min_p_diff:
                                    min_p_diff = diff
                                    best_short_put = k_cand
                            if best_short_put is not None:
                                long_put = best_short_put - self.spread_width
                                short_p = self._bs_put_price(S_t, best_short_put, T_initial, hv_t)
                                long_p = self._bs_put_price(S_t, long_put, T_initial, hv_t)
                                put_spread = (best_short_put, long_put, short_p - long_p)
                                
                        # Find Call strikes if BEARISH or NEUTRAL
                        call_spread = None
                        if regime in ['BEARISH', 'NEUTRAL']:
                            best_short_call = None
                            min_c_diff = 9999
                            min_k = math.floor((S_t * 1.01) / step) * step
                            max_k = math.ceil((S_t * 1.20) / step) * step
                            for k_cand in np.arange(min_k, max_k + step, step):
                                d_call = self._calculate_option_delta(S_t, k_cand, T_initial, hv_t, 'call')
                                diff = abs(d_call - abs(self.target_delta))
                                if diff < min_c_diff:
                                    min_c_diff = diff
                                    best_short_call = k_cand
                            if best_short_call is not None:
                                long_call = best_short_call + self.spread_width
                                short_c = self._bs_call_price(S_t, best_short_call, T_initial, hv_t)
                                long_c = self._bs_call_price(S_t, long_call, T_initial, hv_t)
                                call_spread = (best_short_call, long_call, short_c - long_c)
                                
                        # Enter matching strategy
                        net_credit = 0.0
                        entered = False
                        pos_details = {}
                        
                        if regime == 'BULLISH' and put_spread and put_spread[2] > 0.1:
                            net_credit = put_spread[2]
                            pos_details = {
                                'strategy_type': 'BULL_PUT',
                                'short_strike': put_spread[0],
                                'long_strike': put_spread[1]
                            }
                            entered = True
                        elif regime == 'BEARISH' and call_spread and call_spread[2] > 0.1:
                            net_credit = call_spread[2]
                            pos_details = {
                                'strategy_type': 'BEAR_CALL',
                                'short_strike': call_spread[0],
                                'long_strike': call_spread[1]
                            }
                            entered = True
                        elif regime == 'NEUTRAL' and put_spread and call_spread and put_spread[2] > 0.1 and call_spread[2] > 0.1:
                            net_credit = put_spread[2] + call_spread[2]
                            pos_details = {
                                'strategy_type': 'IRON_CONDOR',
                                'short_put_strike': put_spread[0],
                                'long_put_strike': put_spread[1],
                                'short_call_strike': call_spread[0],
                                'long_call_strike': call_spread[1],
                                'short_strike': put_spread[0], # For generic fallback
                                'long_strike': put_spread[1]
                            }
                            entered = True
                            
                        if entered:
                            max_margin_per_trade = capital * self.max_capital_per_spread_pct
                            one_contract_margin = self.spread_width * self.lot_size
                            contracts = int(max_margin_per_trade // one_contract_margin)
                            
                            if contracts > 0:
                                position = {
                                    'entry_date': index,
                                    'net_credit': net_credit,
                                    'spread_width': self.spread_width,
                                    'contracts': contracts,
                                    'current_dte': self.target_dte,
                                    'initial_underlying': S_t,
                                    **pos_details
                                }
                                in_position = True
                                
        # Calculate metrics
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        win_rate = len(wins) / len(trades) if trades else 0.0
        
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Drawdown
        max_capital, current_capital, max_dd = self.initial_capital, self.initial_capital, 0.0
        for t in trades:
            current_capital += t['pnl']
            if current_capital > max_capital:
                max_capital = current_capital
            dd = (max_capital - current_capital) / max_capital
            if dd > max_dd:
                max_dd = dd
                
        return {
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'max_drawdown': max_dd,
            'final_capital': capital,
            'total_trades': len(trades),
            'trades_log': trades
        }
