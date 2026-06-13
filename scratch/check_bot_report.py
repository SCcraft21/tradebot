import sqlite3
import json
import datetime
from db import load_all_crypto_trades, load_all_stock_spreads, load_closed_trades_history

def main():
    print("=== LIVE TRADING BOT STATUS REPORT ===")
    
    # Load active Crypto positions
    crypto_trades = load_all_crypto_trades()
    print(f"\n[CRYPTO] Active Positions ({len(crypto_trades)}):")
    if not crypto_trades:
        print("  None")
    for sym, t in crypto_trades.items():
        print(f"  - {sym}: {t['amount']} @ {t['entry_price']:.4f} (TP: {t['take_profit']:.4f}, SL: {t['stop_loss']:.4f})")
        
    # Load active Option spreads
    stock_spreads = load_all_stock_spreads()
    print(f"\n[STOCKS] Active Options Spreads ({len(stock_spreads)}):")
    if not stock_spreads:
        print("  None")
    for sym, s in stock_spreads.items():
        contracts = s.get('contracts', 1)
        strat = s.get('strategy_type', 'BULL_PUT')
        print(f"  - {sym} ({contracts} contracts - {strat}):")
        print(f"    DTE: {s.get('dte')} days | Mid Net Credit: Rs.{s.get('net_credit', 0.0):.2f} | Collateral Lockup: Rs.{s.get('margin_lockup', 0.0):.2f}")
        
    # Load recent closed trades
    history = load_closed_trades_history(limit=10)
    print(f"\nRecent Closed Trades ({len(history)}):")
    if not history:
        print("  No closed trades recorded yet.")
    for r in history:
        pnl = r['pnl']
        status_emoji = "Profit" if pnl >= 0 else "Loss"
        closed_time = r['closed_at'].strftime("%Y-%m-%d %H:%M")
        print(f"  - {r['symbol']} ({r['direction']}) | {status_emoji}: Rs.{pnl:.2f} | Reason: {r['reason']} | Closed At: {closed_time}")

if __name__ == "__main__":
    main()
