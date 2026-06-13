import pandas as pd
import numpy as np
import math
import logging
import datetime
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

logger = logging.getLogger(__name__)

class MockFetcher:
    def __init__(self, full_df, hv_series):
        self.full_df = full_df
        self.hv_series = hv_series
        self.current_idx = None
        self.risk_free_rate = 0.045
        
    def fetch_stock_history(self, symbol, period="1y"):
        return self.full_df.loc[:self.current_idx]
        
    def calculate_historical_volatility(self, df, window=20):
        latest_vol = self.hv_series.loc[self.current_idx]
        return latest_vol if not pd.isna(latest_vol) and latest_vol > 0 else 0.25
        
    def calculate_iv_rank(self, df, current_iv):
        # Simulate IV Rank
        return 45.0
        
    def _norm_cdf(self, x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
        
    def calculate_option_delta(self, S, K, T, sigma, option_type='put'):
        if T <= 0:
            return -0.5 if option_type == 'put' else 0.5
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        if option_type == 'put':
            return self._norm_cdf(d1) - 1.0
        return self._norm_cdf(d1)
        
    def _bs_put_price(self, S, K, T, sigma):
        if T <= 0: return max(0.0, K - S)
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(-d2) - S * self._norm_cdf(-d1)
        
    def _bs_call_price(self, S, K, T, sigma):
        if T <= 0: return max(0.0, S - K)
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * self._norm_cdf(d1) - K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(d2)

    def fetch_option_chain(self, symbol, target_dte=40):
        S = self.full_df.loc[self.current_idx, 'Close']
        sigma = self.hv_series.loc[self.current_idx]
        if pd.isna(sigma) or sigma <= 0:
            sigma = 0.25
            
        expiration_date = self.current_idx + datetime.timedelta(days=target_dte)
        expiration = expiration_date.strftime("%Y-%m-%d")
        
        step = 5.0 if S < 500 else 10.0
        min_k = math.floor((S * 0.8) / step) * step
        max_k = math.ceil((S * 1.2) / step) * step
        strikes = np.arange(min_k, max_k + step, step)
        
        T = target_dte / 365.0
        
        puts_data = []
        calls_data = []
        for K in strikes:
            put_price = self._bs_put_price(S, K, T, sigma)
            put_delta = self.calculate_option_delta(S, K, T, sigma, 'put')
            puts_data.append({
                'strike': K,
                'bid': put_price * 0.98,
                'ask': put_price * 1.02,
                'mid': put_price,
                'impliedVolatility': sigma,
                'dte': target_dte,
                'underlying_price': S,
                'delta': put_delta
            })
            
            call_price = self._bs_call_price(S, K, T, sigma)
            call_delta = self.calculate_option_delta(S, K, T, sigma, 'call')
            calls_data.append({
                'strike': K,
                'bid': call_price * 0.98,
                'ask': call_price * 1.02,
                'mid': call_price,
                'impliedVolatility': sigma,
                'dte': target_dte,
                'underlying_price': S,
                'delta': call_delta
            })
            
        return expiration, pd.DataFrame(puts_data), pd.DataFrame(calls_data), S

class StocksOptionsBacktester:
    def __init__(self, target_dte: int = 30, target_delta: float = -0.25, spread_width: float = 100.0, 
                 min_iv_rank: float = 15.0, profit_target_pct: float = 0.40, stop_loss_mult: float = 2.0,
                 risk_free_rate: float = 0.045, initial_capital: float = 1000000.0, max_capital_per_spread_pct: float = 0.40,
                 lot_size: float = 100.0, base_lots: int = 5, sized_down_lots: int = 1, adaptive_sizing: bool = True):
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
        self.base_lots = base_lots
        self.sized_down_lots = sized_down_lots
        self.adaptive_sizing = adaptive_sizing

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _bs_put_price(self, S: float, K: float, T: float, sigma: float) -> float:
        if T <= 0: return max(0.0, K - S)
        if sigma <= 0: sigma = 0.01
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(-d2) - S * self._norm_cdf(-d1)

    def _bs_call_price(self, S: float, K: float, T: float, sigma: float) -> float:
        if T <= 0: return max(0.0, S - K)
        if sigma <= 0: sigma = 0.01
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * self._norm_cdf(d1) - K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(d2)

    def run_backtest(self, historical_data: dict) -> dict:
        """Run options backtest simulating all 7 advanced options strategies."""
        logger.info("Starting stock options backtest simulation...")
        capital = self.initial_capital
        trades = []
        
        # Import dynamically to prevent circular dependencies
        from stocks.options_strategy import BullPutSpreadStrategy
        
        for symbol, df in historical_data.items():
            if df.empty or len(df) < 252:
                logger.warning(f"Insufficient data for {symbol} (requires at least 252 bars).")
                continue
                
            df = df.copy()
            
            # Historical volatility calculation
            log_returns = np.log(df['Close'] / df['Close'].shift(1))
            df['hv'] = log_returns.rolling(window=20).std() * math.sqrt(252)
            
            df = df.dropna()
            if df.empty:
                continue
                
            in_position = False
            position = {}
            
            mock_fetcher = MockFetcher(df, df['hv'])
            strategy = BullPutSpreadStrategy(
                data_fetcher=mock_fetcher,
                target_dte=self.target_dte,
                target_delta=self.target_delta,
                spread_width=self.spread_width,
                min_iv_rank=self.min_iv_rank
            )
            
            # Step through time daily
            for index, row in df.iterrows():
                mock_fetcher.current_idx = index
                S_t = row['Close']
                hv_t = row['hv']
                
                # 1. Manage Active Position
                if in_position:
                    position['current_dte'] -= 1
                    T_t = position['current_dte'] / 365.0
                    strat = position['strategy_type']
                    entry_val = abs(position['net_credit'])
                    is_debit = position['net_credit'] < 0
                    
                    # Compute current value (cost to close position)
                    if strat in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
                        current_spread_val = self._bs_call_price(S_t, position['long_strike'], T_t, hv_t)
                    elif strat in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
                        current_spread_val = self._bs_put_price(S_t, position['long_strike'], T_t, hv_t)
                    elif strat == 'BULL_PUT':
                        sp = self._bs_put_price(S_t, position['short_strike'], T_t, hv_t)
                        lp = self._bs_put_price(S_t, position['long_strike'], T_t, hv_t)
                        current_spread_val = sp - lp
                    elif strat == 'BEAR_CALL':
                        sc = self._bs_call_price(S_t, position['short_strike'], T_t, hv_t)
                        lc = self._bs_call_price(S_t, position['long_strike'], T_t, hv_t)
                        current_spread_val = sc - lc
                    elif strat == 'IRON_BUTTERFLY':
                        sp = self._bs_put_price(S_t, position['short_put_strike'], T_t, hv_t)
                        sc = self._bs_call_price(S_t, position['short_call_strike'], T_t, hv_t)
                        lp = self._bs_put_price(S_t, position['long_put_strike'], T_t, hv_t)
                        lc = self._bs_call_price(S_t, position['long_call_strike'], T_t, hv_t)
                        current_spread_val = (sp + sc) - (lp + lc)
                    elif strat == 'IRON_CONDOR':
                        sp = self._bs_put_price(S_t, position['short_put_strike'], T_t, hv_t)
                        sc = self._bs_call_price(S_t, position['short_call_strike'], T_t, hv_t)
                        lp = self._bs_put_price(S_t, position['long_put_strike'], T_t, hv_t)
                        lc = self._bs_call_price(S_t, position['long_call_strike'], T_t, hv_t)
                        current_spread_val = (sp - lp) + (sc - lc)
                    elif strat == 'CASH_SECURED_PUT':
                        current_spread_val = self._bs_put_price(S_t, position['short_strike'], T_t, hv_t)
                    elif strat == 'COVERED_CALL':
                        call_price = self._bs_call_price(S_t, position['short_strike'], T_t, hv_t)
                        current_spread_val = S_t - call_price
                    elif strat == 'CALENDAR_SPREAD':
                        # Long option has T_t + 30 days remaining
                        long_price = self._bs_call_price(S_t, position['short_strike'], T_t + 30.0/365.0, hv_t)
                        short_price = self._bs_call_price(S_t, position['short_strike'], T_t, hv_t)
                        current_spread_val = long_price - short_price
                    else:
                        current_spread_val = 0.0
                        
                    # Evaluate Exits
                    closed = False
                    reason = ""
                    pnl = 0.0
                    
                    if is_debit:
                        # Profit Target: 50% gain
                        if current_spread_val >= entry_val * (1.0 + self.profit_target_pct):
                            pnl = (current_spread_val - entry_val) * position['contracts'] * self.lot_size
                            closed = True
                            reason = "PROFIT_TARGET"
                        # Stop Loss: 50% loss
                        elif current_spread_val <= entry_val * 0.50:
                            pnl = (current_spread_val - entry_val) * position['contracts'] * self.lot_size
                            closed = True
                            reason = "STOP_LOSS"
                    else:
                        # Profit Target: credit decay
                        if current_spread_val <= entry_val * (1.0 - self.profit_target_pct):
                            pnl = (entry_val - current_spread_val) * position['contracts'] * self.lot_size
                            closed = True
                            reason = "PROFIT_TARGET"
                        # Stop Loss: 2x credit loss
                        elif current_spread_val >= entry_val * self.stop_loss_mult:
                            pnl = (entry_val - current_spread_val) * position['contracts'] * self.lot_size
                            closed = True
                            reason = "STOP_LOSS"
                            
                    # Expiration Settlement
                    if not closed and position['current_dte'] <= 0:
                        settlement_value = 0.0
                        if strat in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
                            settlement_value = max(0.0, S_t - position['long_strike'])
                            pnl = (settlement_value - entry_val) * position['contracts'] * self.lot_size
                        elif strat in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
                            settlement_value = max(0.0, position['long_strike'] - S_t)
                            pnl = (settlement_value - entry_val) * position['contracts'] * self.lot_size
                        elif strat == 'BULL_PUT':
                            settlement_value = max(0.0, position['short_strike'] - S_t) - max(0.0, position['long_strike'] - S_t)
                            pnl = (entry_val - settlement_value) * position['contracts'] * self.lot_size
                        elif strat == 'BEAR_CALL':
                            settlement_value = max(0.0, S_t - position['short_strike']) - max(0.0, S_t - position['long_strike'])
                            pnl = (entry_val - settlement_value) * position['contracts'] * self.lot_size
                        elif strat in ('IRON_CONDOR', 'IRON_BUTTERFLY'):
                            put_loss = max(0.0, position['short_put_strike'] - S_t) - max(0.0, position['long_put_strike'] - S_t)
                            call_loss = max(0.0, S_t - position['short_call_strike']) - max(0.0, S_t - position['long_call_strike'])
                            settlement_value = put_loss + call_loss
                            pnl = (entry_val - settlement_value) * position['contracts'] * self.lot_size
                        elif strat == 'COVERED_CALL':
                            if S_t >= position['short_strike']:
                                pnl = (position['short_strike'] - entry_val) * position['contracts'] * self.lot_size
                            else:
                                pnl = (S_t - entry_val) * position['contracts'] * self.lot_size
                        elif strat == 'CASH_SECURED_PUT':
                            settlement_value = max(0.0, position['short_strike'] - S_t)
                            pnl = (entry_val - settlement_value) * position['contracts'] * self.lot_size
                        elif strat == 'CALENDAR_SPREAD':
                            # Sell back the long leg at current price (30 DTE remaining)
                            long_val = self._bs_call_price(S_t, position['short_strike'], 30.0/365.0, hv_t)
                            pnl = (long_val - entry_val) * position['contracts'] * self.lot_size
                            
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
                    spread_info = strategy.scan_for_spread(symbol)
                    if spread_info:
                        net_credit = spread_info['net_credit']
                        cost_per_contract = abs(net_credit) if spread_info['spread_width'] <= 0 else spread_info['spread_width']
                        
                        max_margin_per_trade = capital * self.max_capital_per_spread_pct
                        one_contract_margin = cost_per_contract * self.lot_size
                        contracts = int(max_margin_per_trade // one_contract_margin) if one_contract_margin > 0 else 0
                        
                        # Dynamic equity-based lot sizing capped at 80% of equity
                        max_allowed_margin = capital * 0.80
                        
                        # Base dynamic lots: up to 80% of equity
                        base_dynamic_lots = int(max_allowed_margin // one_contract_margin) if one_contract_margin > 0 else 0
                        
                        # Sized down dynamic lots (scaled by sized_down_lots / base_lots)
                        size_down_factor = float(self.sized_down_lots) / float(self.base_lots) if self.base_lots > 0 else 0.20
                        sized_down_dynamic_lots = int((capital * 0.80 * size_down_factor) // one_contract_margin) if one_contract_margin > 0 else 0
                        if sized_down_dynamic_lots == 0 and capital >= one_contract_margin:
                            sized_down_dynamic_lots = 1
                            
                        target_lots = base_dynamic_lots
                        if self.adaptive_sizing:
                            last_pnl = 0.0
                            if trades:
                                last_pnl = trades[-1]['pnl']
                            if last_pnl < 0:
                                target_lots = sized_down_dynamic_lots
                                
                        # Capital constraint check
                        if target_lots * one_contract_margin > capital:
                            target_lots = int(capital // one_contract_margin) if one_contract_margin > 0 else 0
                            
                        contracts = target_lots
                        
                        if contracts > 0:
                            position = {
                                'entry_date': index,
                                'net_credit': net_credit,
                                'spread_width': spread_info['spread_width'],
                                'contracts': contracts,
                                'current_dte': spread_info['dte'],
                                'initial_underlying': S_t,
                                'strategy_type': spread_info['strategy_type'],
                                'short_strike': spread_info.get('short_strike', 0.0),
                                'long_strike': spread_info.get('long_strike', 0.0),
                                'short_put_strike': spread_info.get('short_put_strike', 0.0),
                                'long_put_strike': spread_info.get('long_put_strike', 0.0),
                                'short_call_strike': spread_info.get('short_call_strike', 0.0),
                                'long_call_strike': spread_info.get('long_call_strike', 0.0),
                            }
                            in_position = True
                            
        # Calculate metrics
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        win_rate = len(wins) / len(trades) if trades else 0.0
        
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Drawdown calculation
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

