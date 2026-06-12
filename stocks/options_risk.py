import logging
from typing import Dict
from db import load_all_stock_spreads, save_stock_spread, remove_stock_spread

logger = logging.getLogger(__name__)

def get_lot_size(symbol: str, default_lot_size: float = 100.0) -> float:
    # Normalize symbol to uppercase
    sym = symbol.upper()
    
    # Lot sizes for major indices and stocks in Indian Market (NSE)
    nse_lot_sizes = {
        '^NSEI': 50.0,
        'NIFTY': 50.0,
        '^NSEBANK': 15.0,
        'BANKNIFTY': 15.0,
        'RELIANCE.NS': 250.0,
        'TCS.NS': 175.0,
        'INFY.NS': 400.0,
        'HDFCBANK.NS': 550.0,
        'ICICIBANK.NS': 700.0,
        'SBIN.NS': 1500.0,
        'BHARTIARTL.NS': 950.0,
        'ITC.NS': 1600.0,
        'LT.NS': 300.0
    }
    
    # Check direct match
    if sym in nse_lot_sizes:
        return nse_lot_sizes[sym]
        
    # Check if suffix is omitted
    for k, v in nse_lot_sizes.items():
        if k.endswith('.NS') and sym == k[:-3]:
            return v
            
    return default_lot_size

class OptionsRiskManager:
    def __init__(self, max_option_margin_pct: float = 0.50, max_capital_per_spread_pct: float = 0.15,
                 max_active_spreads: int = 3, daily_loss_limit_pct: float = 0.05, lot_size: float = 100.0):
        self.max_option_margin_pct = max_option_margin_pct
        self.max_capital_per_spread_pct = max_capital_per_spread_pct
        self.max_active_spreads = max_active_spreads
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.lot_size = lot_size
        
        self.initial_balance = 0.0
        self.daily_pnl = 0.0
        
        try:
            self.open_spreads = load_all_stock_spreads()
        except Exception as e:
            logger.error(f"Failed to load open spreads from database: {e}")
            self.open_spreads = {}
            
        self.current_collateral = sum(s.get('margin_lockup', 0.0) for s in self.open_spreads.values())

    def set_initial_balance(self, balance: float):
        self.initial_balance = balance

    def register_spread(self, symbol: str, spread_info: dict, contracts: int):
        # Collateral required = spread_width * lot_size * contracts
        width = spread_info['spread_width']
        lot_size = get_lot_size(symbol, self.lot_size)
        margin_lockup = width * lot_size * contracts
        
        spread_record = spread_info.copy()
        spread_record['contracts'] = contracts
        spread_record['margin_lockup'] = margin_lockup
        spread_record['lot_size'] = lot_size
        
        self.open_spreads[symbol] = spread_record
        self.current_collateral += margin_lockup
        
        try:
            save_stock_spread(symbol, spread_record, contracts, margin_lockup)
        except Exception as e:
            logger.error(f"DB Error saving options spread registration for {symbol}: {e}")
            
        logger.info(f"Registered options spread for {symbol} | Contracts: {contracts} | Collateral Locked: ₹{margin_lockup:.2f} | Total Collateral: ₹{self.current_collateral:.2f}")

    def close_spread(self, symbol: str, real_pnl: float, reason: str = 'MANUAL'):
        if symbol in self.open_spreads:
            spread = self.open_spreads.pop(symbol)
            self.current_collateral -= spread['margin_lockup']
            self.daily_pnl += real_pnl
            
            try:
                from db import remove_stock_spread, save_closed_trade
                remove_stock_spread(symbol)
                
                contracts = spread.get('contracts', 1)
                lot_size = spread.get('lot_size', 50)
                denom = contracts * lot_size
                exit_price = spread.get('net_credit', 0.0) - (real_pnl / denom) if denom > 0 else 0.0
                
                save_closed_trade(
                    asset_type='stock_options',
                    symbol=symbol,
                    direction=spread.get('strategy_type', 'BULL_PUT'),
                    amount=float(contracts),
                    entry_price=spread.get('net_credit', 0.0),
                    exit_price=exit_price,
                    pnl=real_pnl,
                    reason=reason
                )
            except Exception as e:
                logger.error(f"DB Error removing/saving options spread for {symbol}: {e}")
                
            logger.info(f"Closed options spread for {symbol} | Released Collateral: ₹{spread['margin_lockup']:.2f} | PnL: ₹{real_pnl:.2f} | Remaining Collateral: ₹{self.current_collateral:.2f} | Reason: {reason}")

    def can_open_spread(self) -> bool:
        if len(self.open_spreads) >= self.max_active_spreads:
            logger.info("Cannot open spread: Max active spreads limit reached.")
            return False
            
        if self.initial_balance > 0:
            # Check if total collateral exceeds max margin allocation
            if (self.current_collateral / self.initial_balance) >= self.max_option_margin_pct:
                logger.info("Cannot open spread: Max option margin allocation reached.")
                return False
                
            # Check daily loss limit
            if (self.daily_pnl / self.initial_balance) <= -self.daily_loss_limit_pct:
                logger.warning("Cannot open spread: Daily loss limit reached. Trading halted.")
                return False
                
        return True

    def calculate_contracts(self, total_balance: float, spread_width: float, symbol: str) -> int:
        """
        Calculate the number of contracts to trade based on max capital per spread and available margin.
        Max margin per trade = total_balance * max_capital_per_spread_pct
        One contract margin = spread_width * lot_size
        """
        lot_size = get_lot_size(symbol, self.lot_size)
        available_margin = total_balance - self.current_collateral
        max_margin_per_trade = total_balance * self.max_capital_per_spread_pct
        one_contract_margin = spread_width * lot_size
        
        if one_contract_margin <= 0 or available_margin < one_contract_margin:
            return 0
            
        limit_margin = min(max_margin_per_trade, available_margin)
        contracts = int(limit_margin // one_contract_margin)
        return contracts
