import logging
import pandas as pd
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, take_profit_pct: float, stop_loss_pct: float, max_capital_per_trade_pct: float, 
                 daily_loss_limit_pct: float, max_open_trades: int,
                 tp_atr_mult: float = 3.0, sl_atr_mult: float = 1.5,
                 adaptive_sizing: bool = False, base_lots: int = 5, sized_down_lots: int = 1):
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_capital_per_trade_pct = max_capital_per_trade_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_open_trades = max_open_trades
        self.tp_atr_mult = tp_atr_mult
        self.sl_atr_mult = sl_atr_mult
        self.adaptive_sizing = adaptive_sizing
        self.base_lots = base_lots
        self.sized_down_lots = sized_down_lots
        
        from db import load_all_crypto_trades
        self.open_trades: Dict[str, Dict] = load_all_crypto_trades() # Tracks active trades state locally
        self.daily_pnl = 0.0
        self.initial_balance = 0.0 

    def set_initial_balance(self, balance: float):
        self.initial_balance = balance
        
    def can_open_trade(self) -> bool:
        if len(self.open_trades) >= self.max_open_trades:
            return False
            
        if self.initial_balance > 0 and (self.daily_pnl / self.initial_balance) <= -self.daily_loss_limit_pct:
            logger.warning("Daily loss limit reached. Trading halted.")
            return False
            
        return True

    def calculate_position_size(self, total_balance: float, current_price: float) -> float:
        if self.adaptive_sizing:
            from db import load_closed_trades_history
            history = load_closed_trades_history(limit=1)
            target_lots = self.base_lots
            if history:
                last_trade = history[0]
                # Check if the last trade was a crypto trade and closed at a loss
                if last_trade.get('asset_type') == 'crypto' and last_trade.get('pnl', 0.0) < 0:
                    target_lots = self.sized_down_lots
                    logger.info(f"Crypto: Last trade was a LOSS (PnL: {last_trade['pnl']:.2f}). Sizing down to {target_lots} lots.")
                else:
                    logger.info(f"Crypto: Last trade was a WIN/TIE. Using base {target_lots} lots.")
            else:
                logger.info(f"Crypto: No trade history. Using base {target_lots} lots.")
                
            lot_fraction = self.max_capital_per_trade_pct / self.base_lots
            capital_to_risk = total_balance * (target_lots * lot_fraction)
        else:
            capital_to_risk = total_balance * self.max_capital_per_trade_pct
            
        return capital_to_risk / current_price

    def calculate_tp_sl(self, entry_price: float, atr: float = None) -> Tuple[float, float]:
        if atr is not None and not pd.isna(atr) and atr > 0:
            take_profit = entry_price + (self.tp_atr_mult * atr)
            stop_loss = entry_price - (self.sl_atr_mult * atr)
            logger.info(f"Calculated ATR-based exits: TP={take_profit:.2f} (Entry+{self.tp_atr_mult}*ATR), SL={stop_loss:.2f} (Entry-{self.sl_atr_mult}*ATR) [ATR={atr:.4f}]")
        else:
            take_profit = entry_price * (1 + self.take_profit_pct)
            stop_loss = entry_price * (1 - self.stop_loss_pct)
            logger.info(f"Calculated Pct-based exits: TP={take_profit:.2f} (+{self.take_profit_pct*100}%), SL={stop_loss:.2f} (-{self.stop_loss_pct*100}%)")
        return take_profit, stop_loss
        
    def register_trade(self, symbol: str, amount: float, entry_price: float, atr: float = None):
        tp, sl = self.calculate_tp_sl(entry_price, atr)
        self.open_trades[symbol] = {
            'amount': amount,
            'entry_price': entry_price,
            'take_profit': tp,
            'stop_loss': sl
        }
        from db import save_crypto_trade
        save_crypto_trade(symbol, amount, entry_price, tp, sl)
        logger.info(f"Registered trade: {symbol} at {entry_price:.2f} | TP: {tp:.2f} | SL: {sl:.2f}")

    def close_trade(self, symbol: str, exit_price: float, reason: str = 'MANUAL'):
        if symbol in self.open_trades:
            trade = self.open_trades.pop(symbol)
            pnl = (exit_price - trade['entry_price']) * trade['amount']
            self.daily_pnl += pnl
            from db import remove_crypto_trade, save_closed_trade
            remove_crypto_trade(symbol)
            save_closed_trade(
                asset_type='crypto',
                symbol=symbol,
                direction='BUY',
                amount=trade['amount'],
                entry_price=trade['entry_price'],
                exit_price=exit_price,
                pnl=pnl,
                reason=reason
            )
            logger.info(f"Closed trade for {symbol} at {exit_price:.2f}. PnL: ₹{pnl:.2f} | Reason: {reason}")

