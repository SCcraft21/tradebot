import logging
import datetime
from typing import Dict, Optional
from db import load_all_stock_spreads

logger = logging.getLogger(__name__)

class OptionsExecution:
    def __init__(self, paper: bool = True, dhan_client=None):
        self.paper = paper
        self.dhan_client = dhan_client
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
            
        strat = spread.get('strategy_type', 'BULL_PUT')
        
        if self.paper:
            entry_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            spread['entry_time'] = entry_time
            spread['current_dte'] = spread['dte']
            self.active_spreads[symbol] = spread
            
            logger.info(f"[PAPER] entered {strat} on {symbol} | Net Credit/Debit: ₹{spread['net_credit']:.2f}")
            return True
        else:
            if not self.dhan_client:
                logger.error("Dhan API: Cannot execute live options order because DhanClient is not initialized.")
                return False
                
            expiry = spread['expiration']
            contracts = spread.get('contracts', 1)
            lot_size = spread.get('lot_size', 50)
            quantity = int(contracts * lot_size)
            
            # Map strategy to legs
            legs = []
            if strat == 'BULL_PUT':
                legs.append({"type": "PE", "strike": spread['short_strike'], "action": "SELL"})
                legs.append({"type": "PE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat == 'BEAR_CALL':
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "SELL"})
                legs.append({"type": "CE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
                legs.append({"type": "CE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
                legs.append({"type": "PE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat == 'CASH_SECURED_PUT':
                legs.append({"type": "PE", "strike": spread['short_strike'], "action": "SELL"})
            elif strat == 'COVERED_CALL':
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "SELL"})
            elif strat == 'CALENDAR_SPREAD':
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "SELL", "expiry": spread.get('near_expiration', expiry)})
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "BUY", "expiry": spread.get('long_expiration', expiry)})
            elif strat == 'IRON_CONDOR':
                legs.append({"type": "PE", "strike": spread['short_put_strike'], "action": "SELL"})
                legs.append({"type": "PE", "strike": spread['long_put_strike'], "action": "BUY"})
                legs.append({"type": "CE", "strike": spread['short_call_strike'], "action": "SELL"})
                legs.append({"type": "CE", "strike": spread['long_call_strike'], "action": "BUY"})
            elif strat == 'IRON_BUTTERFLY':
                legs.append({"type": "PE", "strike": spread['short_put_strike'], "action": "SELL"})
                legs.append({"type": "PE", "strike": spread['long_put_strike'], "action": "BUY"})
                legs.append({"type": "CE", "strike": spread['short_call_strike'], "action": "SELL"})
                legs.append({"type": "CE", "strike": spread['long_call_strike'], "action": "BUY"})
                
            logger.info(f"Dhan API: Resolving security IDs for live {strat} on {symbol}...")
            
            leg_orders = []
            for leg in legs:
                leg_expiry = leg.get('expiry', expiry)
                sec_id = self.dhan_client.find_instrument_id(
                    symbol=symbol,
                    segment='D',
                    strike=leg['strike'],
                    option_type=leg['type'],
                    expiry_date=leg_expiry
                )
                if not sec_id:
                    logger.error(f"Dhan API: Failed to find instrument ID for leg: {leg} on {symbol}. Aborting order.")
                    return False
                leg['security_id'] = sec_id
                leg_orders.append(leg)
                
            logger.info(f"Dhan API: Executing orders for live {strat} on {symbol}...")
            placed_orders = []
            try:
                for leg in leg_orders:
                    res = self.dhan_client.place_order_fno(
                        security_id=leg['security_id'],
                        buy_or_sell=leg['action'],
                        quantity=quantity
                    )
                    if res.get('status') == 'failure':
                        raise RuntimeError(f"Dhan API order failed: {res.get('remarks') or res.get('message')}")
                    
                    order_id = res.get('data', {}).get('orderId')
                    leg['order_id'] = order_id
                    placed_orders.append(leg)
            except Exception as e:
                logger.error(f"Dhan API: Error placing spread order legs: {e}")
                logger.warning("Dhan API: Reversing already placed legs to manage risk...")
                for placed in placed_orders:
                    try:
                        reverse_action = 'SELL' if placed['action'] == 'BUY' else 'BUY'
                        self.dhan_client.place_order_fno(
                            security_id=placed['security_id'],
                            buy_or_sell=reverse_action,
                            quantity=quantity
                        )
                    except Exception as rev_err:
                        logger.error(f"Dhan API: Failed to place risk reversal order for leg {placed}: {rev_err}")
                return False
                
            entry_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            spread['entry_time'] = entry_time
            spread['current_dte'] = spread['dte']
            spread['placed_legs'] = placed_orders
            self.active_spreads[symbol] = spread
            
            logger.info(f"Dhan API: Successfully entered live {strat} on {symbol} | Net Credit/Debit: ₹{spread['net_credit']:.2f}")
            return True

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
        if symbol not in self.active_spreads:
            return None
            
        spread = self.active_spreads[symbol]
        strat = spread.get('strategy_type', 'BULL_PUT')
        
        if self.paper:
            return self.active_spreads.pop(symbol)
            
        if not self.dhan_client:
            logger.error("Dhan API: Cannot close live spread because DhanClient is not initialized.")
            return self.active_spreads.pop(symbol)
            
        logger.info(f"Dhan API: Closing live {strat} on {symbol}...")
        
        placed_legs = spread.get('placed_legs')
        if not placed_legs:
            # Reconstruct legs
            legs = []
            expiry = spread['expiration']
            if strat == 'BULL_PUT':
                legs.append({"type": "PE", "strike": spread['short_strike'], "action": "SELL"})
                legs.append({"type": "PE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat == 'BEAR_CALL':
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "SELL"})
                legs.append({"type": "CE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
                legs.append({"type": "CE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
                legs.append({"type": "PE", "strike": spread['long_strike'], "action": "BUY"})
            elif strat == 'CASH_SECURED_PUT':
                legs.append({"type": "PE", "strike": spread['short_strike'], "action": "SELL"})
            elif strat == 'COVERED_CALL':
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "SELL"})
            elif strat == 'CALENDAR_SPREAD':
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "SELL", "expiry": spread.get('near_expiration', expiry)})
                legs.append({"type": "CE", "strike": spread['short_strike'], "action": "BUY", "expiry": spread.get('long_expiration', expiry)})
            elif strat == 'IRON_CONDOR':
                legs.append({"type": "PE", "strike": spread['short_put_strike'], "action": "SELL"})
                legs.append({"type": "PE", "strike": spread['long_put_strike'], "action": "BUY"})
                legs.append({"type": "CE", "strike": spread['short_call_strike'], "action": "SELL"})
                legs.append({"type": "CE", "strike": spread['long_call_strike'], "action": "BUY"})
            elif strat == 'IRON_BUTTERFLY':
                legs.append({"type": "PE", "strike": spread['short_put_strike'], "action": "SELL"})
                legs.append({"type": "PE", "strike": spread['long_put_strike'], "action": "BUY"})
                legs.append({"type": "CE", "strike": spread['short_call_strike'], "action": "SELL"})
                legs.append({"type": "CE", "strike": spread['long_call_strike'], "action": "BUY"})
                
            placed_legs = []
            for leg in legs:
                leg_expiry = leg.get('expiry', expiry)
                sec_id = self.dhan_client.find_instrument_id(
                    symbol=symbol,
                    segment='D',
                    strike=leg['strike'],
                    option_type=leg['type'],
                    expiry_date=leg_expiry
                )
                if sec_id:
                    leg['security_id'] = sec_id
                    placed_legs.append(leg)
                    
        contracts = spread.get('contracts', 1)
        lot_size = spread.get('lot_size', 50)
        quantity = int(contracts * lot_size)
        
        for leg in placed_legs:
            if 'security_id' in leg:
                close_action = 'BUY' if leg['action'] == 'SELL' else 'SELL'
                try:
                    self.dhan_client.place_order_fno(
                        security_id=leg['security_id'],
                        buy_or_sell=close_action,
                        quantity=quantity
                    )
                except Exception as e:
                    logger.error(f"Dhan API: Error placing close order for leg {leg}: {e}")
                    
        return self.active_spreads.pop(symbol)

