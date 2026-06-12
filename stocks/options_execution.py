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

    def execute_sell_spread(self, spread: dict) -> bool:
        """Execute (or simulate execution of) a credit spread entry."""
        symbol = spread['symbol']
        if symbol in self.active_spreads:
            logger.warning(f"Spread already open for {symbol}. Cannot double entry in current configuration.")
            return False
            
        if self.paper:
            entry_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            spread['entry_time'] = entry_time
            spread['current_dte'] = spread['dte']
            self.active_spreads[symbol] = spread
            strat = spread.get('strategy_type', 'BULL_PUT')
            if strat == 'BULL_PUT':
                logger.info(f"[PAPER] entered BULL PUT SPREAD on {symbol}: "
                            f"Sell {spread['short_strike']:.2f} Put, Buy {spread['long_strike']:.2f} Put "
                            f"for Net Credit ₹{spread['net_credit']:.2f} (Max Profit: ₹{spread['max_profit']:.2f}, "
                            f"Max Loss: ₹{spread['max_loss']:.2f})")
            elif strat == 'BEAR_CALL':
                logger.info(f"[PAPER] entered BEAR CALL SPREAD on {symbol}: "
                            f"Sell {spread['short_strike']:.2f} Call, Buy {spread['long_strike']:.2f} Call "
                            f"for Net Credit ₹{spread['net_credit']:.2f} (Max Profit: ₹{spread['max_profit']:.2f}, "
                            f"Max Loss: ₹{spread['max_loss']:.2f})")
            else: # IRON_CONDOR
                logger.info(f"[PAPER] entered IRON CONDOR on {symbol}: "
                            f"Put Sell {spread['short_put_strike']:.2f} / Buy {spread['long_put_strike']:.2f} | "
                            f"Call Sell {spread['short_call_strike']:.2f} / Buy {spread['long_call_strike']:.2f} "
                            f"for Net Credit ₹{spread['net_credit']:.2f} (Max Profit: ₹{spread['max_profit']:.2f}, "
                            f"Max Loss: ₹{spread['max_loss']:.2f})")
            return True
        else:
            # For real broker execution, we would call API endpoints to place a limit order for the combo.
            logger.error("Live execution for options is currently disabled (only simulated paper trading is supported).")
            return False

    def check_exit_conditions(self, symbol: str, current_underlying_price: float, 
                              current_spread_value: float, profit_target_pct: float = 0.50, 
                              stop_loss_mult: float = 2.0) -> Optional[dict]:
        """
        Evaluate if an open spread needs to be closed based on exit rules.
        Exit rules:
        1. Profit target reached: Buy back spread at (1 - profit_target_pct) * net_credit (e.g. 50% profit).
        2. Stop loss reached: Buy back spread at stop_loss_mult * net_credit (e.g. 2x premium loss).
        3. Expiration: Close out/settle options.
        """
        if symbol not in self.active_spreads:
            return None
            
        spread = self.active_spreads[symbol]
        net_credit = spread['net_credit']
        
        # Current spread value is what it would cost to BUY BACK the spread (close it)
        # 1. Profit Target
        target_buyback_price = net_credit * (1.0 - profit_target_pct)
        if current_spread_value <= target_buyback_price:
            pnl = net_credit - current_spread_value
            spread['exit_price'] = current_spread_value
            spread['pnl'] = pnl
            spread['reason'] = 'PROFIT_TARGET'
            logger.info(f"🎯 Profit target reached for {symbol}: Closed at ₹{current_spread_value:.2f} | PnL: ₹{pnl:.2f}")
            return self.close_spread(symbol)
            
        # 2. Stop Loss
        stop_loss_price = net_credit * stop_loss_mult
        if current_spread_value >= stop_loss_price:
            pnl = net_credit - current_spread_value
            spread['exit_price'] = current_spread_value
            spread['pnl'] = pnl
            spread['reason'] = 'STOP_LOSS'
            logger.info(f"🛑 Stop loss reached for {symbol}: Closed at ₹{current_spread_value:.2f} | PnL: ₹{pnl:.2f}")
            return self.close_spread(symbol)
            
        # 3. Expiration check (DTE <= 0)
        if spread['current_dte'] <= 0:
            strategy_type = spread.get('strategy_type', 'BULL_PUT')
            
            if strategy_type == 'BULL_PUT':
                short_strike = spread['short_strike']
                long_strike = spread['long_strike']
                if current_underlying_price >= short_strike:
                    settlement_value = 0.0
                elif current_underlying_price <= long_strike:
                    settlement_value = short_strike - long_strike
                else:
                    settlement_value = short_strike - current_underlying_price
                    
            elif strategy_type == 'BEAR_CALL':
                short_strike = spread['short_strike']
                long_strike = spread['long_strike']
                if current_underlying_price <= short_strike:
                    settlement_value = 0.0
                elif current_underlying_price >= long_strike:
                    settlement_value = long_strike - short_strike
                else:
                    settlement_value = current_underlying_price - short_strike
                    
            else:  # IRON_CONDOR
                short_put = spread['short_put_strike']
                long_put = spread['long_put_strike']
                short_call = spread['short_call_strike']
                long_call = spread['long_call_strike']
                
                # Check Put side
                if current_underlying_price <= long_put:
                    put_loss = short_put - long_put
                elif current_underlying_price < short_put:
                    put_loss = short_put - current_underlying_price
                else:
                    put_loss = 0.0
                    
                # Check Call side
                if current_underlying_price >= long_call:
                    call_loss = long_call - short_call
                elif current_underlying_price > short_call:
                    call_loss = current_underlying_price - short_call
                else:
                    call_loss = 0.0
                    
                settlement_value = put_loss + call_loss
                
            pnl = net_credit - settlement_value
            spread['exit_price'] = settlement_value
            spread['pnl'] = pnl
            spread['reason'] = 'EXPIRATION'
            logger.info(f"⏳ Spread expired for {symbol}: Underlying Price ₹{current_underlying_price:.2f} | "
                        f"Settlement Value: ₹{settlement_value:.2f} | PnL: ₹{pnl:.2f}")
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
