import pandas as pd
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class Backtester:
    def __init__(self, strategy_obj, risk_manager_obj, initial_capital: float = 10000.0):
        self.strategy = strategy_obj
        self.risk_manager = risk_manager_obj
        self.initial_capital = initial_capital
        
    def run(self, historical_data: Dict[str, pd.DataFrame]):
        logger.info("Starting walk-forward sim backtest...")
        capital = self.initial_capital
        trades = []
        
        for symbol, df in historical_data.items():
            df_signals = self.strategy.generate_signals(df)
            
            in_position = False
            entry_price, amount, tp, sl = 0.0, 0.0, 0.0, 0.0
            
            # Step through historical data mimicking real time behavior
            for index, row in df_signals.iterrows():
                # End Position checks using intrabar high/low proxies
                if in_position:
                    if row['high'] >= tp:
                        capital += amount * (tp - entry_price)
                        trades.append({'symbol': symbol, 'pnl': amount * (tp - entry_price)})
                        in_position = False
                    elif row['low'] <= sl:
                        capital += amount * (sl - entry_price)
                        trades.append({'symbol': symbol, 'pnl': amount * (sl - entry_price)})
                        in_position = False
                
                # Entry Position Check
                if not in_position and row['buy_signal']:
                    amount = (capital * self.risk_manager.max_capital_per_trade_pct) / row['close']
                    entry_price = row['close']
                    atr_val = row.get('atr', None)
                    tp, sl = self.risk_manager.calculate_tp_sl(entry_price, atr=atr_val)
                    in_position = True
        
        # Calculate key metrics
        wins = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        win_rate = len(wins) / len(trades) if trades else 0.0
        
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # simplified peak-to-trough max drawdown
        max_capital, current_capital, max_dd = self.initial_capital, self.initial_capital, 0.0
        for t in trades:
            current_capital += t['pnl']
            if current_capital > max_capital: max_capital = current_capital
            dd = (max_capital - current_capital) / max_capital
            if dd > max_dd: max_dd = dd
                
        return {
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'max_drawdown': max_dd,
            'final_capital': capital,
            'total_trades': len(trades)
        }
