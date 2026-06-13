import urllib.request
import csv
import io
import logging
from db import load_all_stock_spreads, save_stock_spread, remove_stock_spread

logger = logging.getLogger(__name__)

_cached_lot_sizes = {}

def fetch_fo_lot_sizes() -> dict:
    global _cached_lot_sizes
    if _cached_lot_sizes:
        return _cached_lot_sizes
        
    default_lots = {
        '^NSEI': 50.0,
        'NIFTY': 50.0,
        '^NSEBANK': 15.0,
        'BANKNIFTY': 15.0,
        'FINNIFTY': 40.0,
        'RELIANCE': 250.0,
        'TCS': 175.0,
        'INFY': 400.0,
        'HDFCBANK': 550.0,
        'ICICIBANK': 700.0,
        'SBIN': 1500.0,
        'BHARTIARTL': 950.0,
        'ITC': 1600.0,
        'LT': 300.0,
        'AXISBANK': 625.0,
        'KOTAKBANK': 400.0,
        'HINDUNILVR': 300.0,
        'MARUTI': 100.0,
        'TATASTEEL': 5500.0,
        'M&M': 350.0,
        'LTIM': 150.0,
        'BAJFINANCE': 125.0,
        'BAJAJFINSV': 500.0,
        'SUNPHARMA': 700.0,
        'ADANIENT': 250.0,
        'ADANIPORTS': 400.0,
        'ULTRACEMCO': 100.0,
        'WIPRO': 1500.0,
        'HCLTECH': 700.0,
        'ASIANPAINT': 200.0,
        'ONGC': 3850.0,
        'POWERGRID': 3600.0,
        'NTPC': 3000.0,
        'COALINDIA': 4200.0,
        'JIOFIN': 2000.0
    }
    
    url = "https://archives.nseindia.com/content/fo/fo_mktlots.csv"
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            content_type = response.info().get_content_type()
            if 'pdf' in content_type.lower():
                raise ValueError("NSE redirected CSV request to a PDF file")
                
            csv_data = response.read().decode('latin-1').replace('\x00', '')
            reader = csv.reader(csv_data.splitlines())
            
            header = next(reader)
            header = [h.strip().upper() for h in header]
            
            underlying_idx = -1
            lot_size_idx = -1
            
            for i, h in enumerate(header):
                if 'UNDERLYING' in h:
                    underlying_idx = i
                elif 'LOT' in h or 'SIZE' in h:
                    lot_size_idx = i
                    
            if underlying_idx == -1 or lot_size_idx == -1:
                underlying_idx = 1
                lot_size_idx = 2
                
            res = {}
            for row in reader:
                if len(row) > max(underlying_idx, lot_size_idx):
                    sym = row[underlying_idx].strip().upper()
                    val_str = row[lot_size_idx].strip()
                    try:
                        lot_size = int(float(val_str.replace(',', '')))
                        res[sym] = float(lot_size)
                    except ValueError:
                        pass
            if res:
                _cached_lot_sizes = res
                logger.info(f"Loaded {len(res)} lot sizes dynamically from NSE F&O lots CSV.")
                return res
    except Exception as e:
        logger.warning(f"Unable to fetch dynamic NSE F&O lot sizes (falling back to cached local defaults): {e}")
        
    _cached_lot_sizes = default_lots
    return _cached_lot_sizes

def get_lot_size(symbol: str, default_lot_size: float = 100.0) -> float:
    # Normalize symbol to uppercase
    sym = symbol.upper()
    clean_sym = sym
    if clean_sym.endswith('.NS'):
        clean_sym = clean_sym[:-3]
    if '/' in clean_sym:
        clean_sym = clean_sym.split('/')[0]
        
    # Attempt to load dynamically from CSV
    dynamic_lots = fetch_fo_lot_sizes()
    if clean_sym in dynamic_lots:
        return dynamic_lots[clean_sym]
        
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
    if clean_sym in nse_lot_sizes:
        return nse_lot_sizes[clean_sym]
    for k, v in nse_lot_sizes.items():
        if k.endswith('.NS') and clean_sym == k[:-3]:
            return v
            
    return default_lot_size

class OptionsRiskManager:
    def __init__(self, max_option_margin_pct: float = 0.50, max_capital_per_spread_pct: float = 0.15,
                 max_active_spreads: int = 3, daily_loss_limit_pct: float = 0.05, lot_size: float = 100.0,
                 adaptive_sizing: bool = False, base_lots: int = 5, sized_down_lots: int = 1):
        self.max_option_margin_pct = max_option_margin_pct
        self.max_capital_per_spread_pct = max_capital_per_spread_pct
        self.max_active_spreads = max_active_spreads
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.lot_size = lot_size
        self.adaptive_sizing = adaptive_sizing
        self.base_lots = base_lots
        self.sized_down_lots = sized_down_lots
        
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
        width = spread_info.get('spread_width', 0.0)
        # If buying option or calendar spread, collateral could be the net premium cost instead of width
        if width <= 0:
            width = abs(spread_info.get('net_credit', 0.0)) # net premium paid
            
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
            if (self.current_collateral / self.initial_balance) >= self.max_option_margin_pct:
                logger.info("Cannot open spread: Max option margin allocation reached.")
                return False
                
            if (self.daily_pnl / self.initial_balance) <= -self.daily_loss_limit_pct:
                logger.warning("Cannot open spread: Daily loss limit reached. Trading halted.")
                return False
                
        return True

    def calculate_contracts(self, total_balance: float, cost_per_contract: float, symbol: str) -> int:
        """
        Calculate contracts based on cost_per_contract (which is spread_width for credit spreads,
        or option premium for buying single options).
        """
        if self.adaptive_sizing:
            from db import load_closed_trades_history
            history = load_closed_trades_history(limit=1)
            target_lots = self.base_lots
            if history:
                last_trade = history[0]
                if last_trade.get('pnl', 0.0) < 0:
                    target_lots = self.sized_down_lots
                    logger.info(f"Stocks Options: Last trade was a LOSS (PnL: {last_trade['pnl']:.2f}). Sizing down to {target_lots} lots.")
                else:
                    logger.info(f"Stocks Options: Last trade was a WIN/TIE (PnL: {last_trade.get('pnl', 0.0):.2f}). Using base {target_lots} lots.")
            else:
                logger.info("Stocks Options: No trade history. Using base 5 lots.")
        else:
            lot_size = get_lot_size(symbol, self.lot_size)
            available_margin = total_balance - self.current_collateral
            max_margin_per_trade = total_balance * self.max_capital_per_spread_pct
            one_contract_margin = cost_per_contract * lot_size
            if one_contract_margin <= 0 or available_margin < one_contract_margin:
                return 0
            limit_margin = min(max_margin_per_trade, available_margin)
            return int(limit_margin // one_contract_margin)

        lot_size = get_lot_size(symbol, self.lot_size)
        one_contract_margin = cost_per_contract * lot_size
        required_margin = target_lots * one_contract_margin
        available_margin = total_balance - self.current_collateral
        
        if required_margin > available_margin:
            max_possible = int(available_margin // one_contract_margin)
            logger.warning(f"Required margin/capital ₹{required_margin:.2f} for {target_lots} contracts exceeds available margin/capital ₹{available_margin:.2f}. Scaling down to {max_possible} contracts.")
            return max_possible
            
        return target_lots
