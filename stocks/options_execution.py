import logging
import datetime
from typing import Dict, Optional
from db import load_all_stock_spreads

logger = logging.getLogger(__name__)

class OptionsExecution:
    def __init__(self, paper: bool = True):
        self.paper = paper
        try:
            self.active_spreads: Dict[str, dict] = load_all_stock_spreads()
        except Exception as e:
            logger.error(f"Failed to load active spreads from database: {e}")
            self.active_spreads = {}

    def _norm_cdf(self, x: float) -> float:
        import math
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _bs_put_price(self, S: float, K: float, T: float, sigma: float, r: float = 0.045) -> float:
        import math
        if T <= 0:
            return max(0.0, K - S)
        if sigma <= 0:
            sigma = 0.01
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return K * math.exp(-r * T) * self._norm_cdf(-d2) - S * self._norm_cdf(-d1)

    def _bs_call_price(self, S: float, K: float, T: float, sigma: float, r: float = 0.045) -> float:
        import math
        if T <= 0:
            return max(0.0, S - K)
        if sigma <= 0:
            sigma = 0.01
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * self._norm_cdf(d1) - K * math.exp(-r * T) * self._norm_cdf(d2)

    def execute_sell_spread(self, spread: dict) -> bool:
        """Execute (or simulate execution of) an option strategy entry."""
        symbol = spread['symbol']
        if symbol in self.active_spreads:
            logger.warning(f"Strategy already open for {symbol}. Cannot double entry in current configuration.")
            return False
            
        if self.paper:
            entry_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            spread['entry_time'] = entry_time
            spread['current_dte'] = spread['dte']
            self.active_spreads[symbol] = spread
            strat = spread.get('strategy_type', 'BULL_PUT')
            
            logger.info(f"[PAPER] entered {strat} on {symbol} | Net Credit/Debit: ₹{spread['net_credit']:.2f}")
            return True
        else:
            logger.error("Live execution for options is currently disabled (only simulated paper trading is supported).")
            return False

    def check_exit_conditions(self, symbol: str, current_underlying_price: float, 
                               current_spread_value: float, profit_target_pct: float = 0.50, 
                               stop_loss_mult: float = 2.0) -> Optional[dict]:
        """
        Evaluate if an open option strategy needs to be closed based on exit rules.
        Exit rules:
        - For Net Credit strategies (selling options):
          1. Profit target reached: Buy back when value <= (1 - profit_target_pct) * net_credit.
          2. Stop loss reached: Buy back when value >= stop_loss_mult * net_credit.
          3. Expiration reached (DTE <= 0): Settle payouts.
        - For Net Debit strategies (buying options):
          1. Profit target reached: Sell back when value >= (1 + profit_target_pct) * abs(net_credit).
          2. Stop loss reached: Sell back when value <= 0.50 * abs(net_credit) (50% loss).
          3. Expiration reached (DTE <= 0): Settle payouts.
        """
        if symbol not in self.active_spreads:
            return None
            
        spread = self.active_spreads[symbol]
        net_credit = spread['net_credit']
        strat = spread.get('strategy_type', 'BULL_PUT')
        
        is_debit = net_credit < 0
        entry_val = abs(net_credit)
        
        # 1. Profit Target Check
        if is_debit:
            target_value = entry_val * (1.0 + profit_target_pct)
            if current_spread_value >= target_value:
                pnl = current_spread_value - entry_val
                spread['exit_price'] = current_spread_value
                spread['pnl'] = pnl
                spread['reason'] = 'PROFIT_TARGET'
                logger.info(f"🎯 Debit Profit target reached for {symbol} ({strat}): Closed at ₹{current_spread_value:.2f} | PnL: ₹{pnl:.2f}")
                return self.close_spread(symbol)
        else:
            target_value = entry_val * (1.0 - profit_target_pct)
            if current_spread_value <= target_value:
                pnl = entry_val - current_spread_value
                spread['exit_price'] = current_spread_value
                spread['pnl'] = pnl
                spread['reason'] = 'PROFIT_TARGET'
                logger.info(f"🎯 Credit Profit target reached for {symbol} ({strat}): Closed at ₹{current_spread_value:.2f} | PnL: ₹{pnl:.2f}")
                return self.close_spread(symbol)
                
        # 2. Stop Loss Check
        if is_debit:
            # 50% stop loss for long options
            stop_value = entry_val * 0.50
            if current_spread_value <= stop_value:
                pnl = current_spread_value - entry_val
                spread['exit_price'] = current_spread_value
                spread['pnl'] = pnl
                spread['reason'] = 'STOP_LOSS'
                logger.info(f"🛑 Debit Stop loss reached for {symbol} ({strat}): Closed at ₹{current_spread_value:.2f} | PnL: ₹{pnl:.2f}")
                return self.close_spread(symbol)
        else:
            stop_value = entry_val * stop_loss_mult
            if current_spread_value >= stop_value:
                pnl = entry_val - current_spread_value
                spread['exit_price'] = current_spread_value
                spread['pnl'] = pnl
                spread['reason'] = 'STOP_LOSS'
                logger.info(f"🛑 Credit Stop loss reached for {symbol} ({strat}): Closed at ₹{current_spread_value:.2f} | PnL: ₹{pnl:.2f}")
                return self.close_spread(symbol)
                
        # 3. Expiration Check (DTE <= 0)
        if spread['current_dte'] <= 0:
            S = current_underlying_price
            settlement_value = 0.0
            
            if strat in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
                settlement_value = max(0.0, S - spread['long_strike'])
                pnl = settlement_value - entry_val
                
            elif strat in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
                settlement_value = max(0.0, spread['long_strike'] - S)
                pnl = settlement_value - entry_val
                
            elif strat == 'BULL_PUT':
                if S >= spread['short_strike']:
                    settlement_value = 0.0
                elif S <= spread['long_strike']:
                    settlement_value = spread['spread_width']
                else:
                    settlement_value = spread['short_strike'] - S
                pnl = entry_val - settlement_value
                
            elif strat == 'BEAR_CALL':
                if S <= spread['short_strike']:
                    settlement_value = 0.0
                elif S >= spread['long_strike']:
                    settlement_value = spread['spread_width']
                else:
                    settlement_value = S - spread['short_strike']
                pnl = entry_val - settlement_value
                
            elif strat == 'IRON_CONDOR':
                put_loss = max(0.0, spread['short_put_strike'] - S) - max(0.0, spread['long_put_strike'] - S)
                call_loss = max(0.0, S - spread['short_call_strike']) - max(0.0, S - spread['long_call_strike'])
                settlement_value = put_loss + call_loss
                pnl = entry_val - settlement_value
                
            elif strat == 'IRON_BUTTERFLY':
                put_loss = max(0.0, spread['short_put_strike'] - S) - max(0.0, spread['long_put_strike'] - S)
                call_loss = max(0.0, S - spread['short_call_strike']) - max(0.0, S - spread['long_call_strike'])
                settlement_value = put_loss + call_loss
                pnl = entry_val - settlement_value
                
            elif strat == 'COVERED_CALL':
                # Covered Call payout = S_at_expiration - entry_cost if not assigned, else short_strike - entry_cost
                if S >= spread['short_strike']:
                    pnl = spread['short_strike'] - entry_val
                else:
                    pnl = S - entry_val
                settlement_value = S
                
            elif strat == 'CASH_SECURED_PUT':
                # CSP payout = entry_val - settlement_value_of_put_debt
                settlement_value = max(0.0, spread['short_strike'] - S)
                pnl = entry_val - settlement_value
                
            elif strat == 'CALENDAR_SPREAD':
                # Settle calendar spread: Sell back the long option (with 30 days remaining)
                # S is underlying, strike is short_strike. Near-term is worthless (or max(0, S-K)).
                # Estimate price of long option (30 DTE, 25% vol proxy)
                long_val = self._bs_call_price(S, spread['short_strike'], 30.0/365.0, 0.25)
                pnl = long_val - entry_val
                settlement_value = long_val
                
            else:
                pnl = 0.0
                
            spread['exit_price'] = settlement_value
            spread['pnl'] = pnl
            spread['reason'] = 'EXPIRATION'
            logger.info(f"⏳ Strategy {strat} expired for {symbol} | Underlying: ₹{S:.2f} | PnL: ₹{pnl:.2f}")
            return self.close_spread(symbol)
            
        return None

    def update_dte(self, symbol: str, days_passed: int = 1) -> int:
        """Simulate time decay by reducing DTE."""
        if symbol in self.active_spreads:
            self.active_spreads[symbol]['current_dte'] -= days_passed
            return self.active_spreads[symbol]['current_dte']
        return 0

    def close_spread(self, symbol: str) -> Optional[dict]:
        """Remove spread from active tracking and return closed position details."""
        if symbol in self.active_spreads:
            return self.active_spreads.pop(symbol)
        return None

