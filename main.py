import argparse
import sys
import yaml
import time

# Configure stdout and stderr to handle UTF-8 symbols like ₹
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
import signal
import logging
from logging.handlers import RotatingFileHandler
from data import DataFetcher
from strategy import SwingStrategy
from execution import Execution
from risk import RiskManager
from backtest import Backtester
from notifications import Notifier
from db import init_db, save_stock_spread

# Stocks options module imports
from stocks.data_fetcher import StockDataFetcher
from stocks.options_strategy import BullPutSpreadStrategy
from stocks.options_execution import OptionsExecution
from stocks.options_risk import OptionsRiskManager, get_lot_size
from stocks.backtest import StocksOptionsBacktester
from telegram_control import TelegramController
from brain import TradingBrain


def setup_logging(config):
    from security_utils import RedactingFormatter
    log_config = config.get('logging', {})
    level = getattr(logging, log_config.get('level', 'INFO').upper(), logging.INFO)
    file_name = log_config.get('file', 'trading_bot.log')
    
    credentials = config.get('credentials', {})
    formatter = RedactingFormatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', credentials=credentials)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root_logger.addHandler(ch)

    # Rotating File
    fh = RotatingFileHandler(file_name, maxBytes=log_config.get('max_bytes', 10485760), backupCount=log_config.get('backup_count', 5), encoding='utf-8')
    fh.setFormatter(formatter)
    root_logger.addHandler(fh)

def load_config(path='config.yaml'):
    import os
    with open(path, 'r') as f:
        config = yaml.safe_load(f)
        
    if 'credentials' not in config:
        config['credentials'] = {}
        
    # Environment variable overrides for secure cloud/Docker hosting
    if 'TELEGRAM_TOKEN' in os.environ:
        config['credentials']['telegram_token'] = os.environ['TELEGRAM_TOKEN']
    if 'TELEGRAM_CHAT_ID' in os.environ:
        config['credentials']['telegram_chat_id'] = os.environ['TELEGRAM_CHAT_ID']
    if 'BYBIT_API_KEY' in os.environ:
        config['credentials']['api_key'] = os.environ['BYBIT_API_KEY']
    if 'BYBIT_API_SECRET' in os.environ:
        config['credentials']['api_secret'] = os.environ['BYBIT_API_SECRET']
    if 'GEMINI_API_KEY' in os.environ:
        config['credentials']['gemini_api_key'] = os.environ['GEMINI_API_KEY']
        
    return config

class TradingBotEngine:
    def __init__(self, config, asset_mode='crypto'):
        self.config = config
        self.asset_mode = asset_mode
        self.trading_active = True
        self.running = True
        
        self.mode = config['trading'].get('mode', 'paper').lower()
        self.is_paper = self.mode != 'live'
        
        # Crypto Components
        demo_trading = config.get('trading', {}).get('demo_trading', False)
        self.crypto_fetcher = DataFetcher(
            config['trading']['exchange'], 
            config['credentials']['api_key'], 
            config['credentials']['api_secret'], 
            paper=self.is_paper,
            demo_trading=demo_trading,
            api_url=config.get('trading', {}).get('api_url')
        )
        strat_cfg = config.get('strategy', {})
        self.crypto_strategy = SwingStrategy(
            strat_cfg.get('rsi_period', 14), 
            strat_cfg.get('rsi_oversold', 35.0), 
            strat_cfg.get('ema_period', 50), 
            strat_cfg.get('ema_proximity_pct', 0.015),
            vwap_period=strat_cfg.get('vwap_period', 14),
            require_volume_spike=strat_cfg.get('require_volume_spike', True),
            require_macd=strat_cfg.get('require_macd', True),
            require_ema200=strat_cfg.get('require_ema200', True)
        )
        self.crypto_execution = Execution(self.crypto_fetcher.exchange, self.is_paper)
        risk_cfg = config.get('risk', {})
        self.crypto_risk = RiskManager(
            risk_cfg.get('take_profit_pct', 0.05), 
            risk_cfg.get('stop_loss_pct', 0.025), 
            risk_cfg.get('max_capital_per_trade_pct', 0.15), 
            risk_cfg.get('daily_loss_limit_pct', 0.10), 
            max_open_trades=risk_cfg.get('max_open_trades', 2),
            tp_atr_mult=risk_cfg.get('tp_atr_mult', 3.0),
            sl_atr_mult=risk_cfg.get('sl_atr_mult', 1.5),
            adaptive_sizing=risk_cfg.get('adaptive_sizing', False),
            base_lots=risk_cfg.get('base_lots', 5),
            sized_down_lots=risk_cfg.get('sized_down_lots', 1)
        )
        
        # Cognitive Brain Integration
        brain_cfg = config.get('brain', {})
        self.brain_enabled = brain_cfg.get('enabled', False)
        self.veto_power = brain_cfg.get('veto_power', True)
        self.crypto_brain = TradingBrain(
            api_key=config['credentials'].get('gemini_api_key', ''),
            model=brain_cfg.get('model', 'gemini-1.5-flash'),
            veto_power=self.veto_power
        )
        
        # Stocks Options Components
        stocks_cfg = config.get('stocks', {})
        self.stocks_fetcher = StockDataFetcher()
        self.stocks_strategy = BullPutSpreadStrategy(
            data_fetcher=self.stocks_fetcher,
            target_dte=stocks_cfg.get('target_dte', 40),
            target_delta=stocks_cfg.get('target_delta', -0.30),
            spread_width=stocks_cfg.get('spread_width', 5.0),
            min_iv_rank=stocks_cfg.get('min_iv_rank', 0.0),
            ema_period=stocks_cfg.get('ema_period', 200)
        )
        self.stocks_execution = OptionsExecution(paper=True)
        self.stocks_risk = OptionsRiskManager(
            max_option_margin_pct=stocks_cfg.get('max_option_margin_pct', 0.50),
            max_capital_per_spread_pct=stocks_cfg.get('max_capital_per_spread_pct', 0.15),
            max_active_spreads=stocks_cfg.get('max_active_spreads', 3),
            daily_loss_limit_pct=stocks_cfg.get('daily_loss_limit_pct', 0.05),
            lot_size=stocks_cfg.get('lot_size', 100.0),
            adaptive_sizing=stocks_cfg.get('adaptive_sizing', False),
            base_lots=stocks_cfg.get('base_lots', 5),
            sized_down_lots=stocks_cfg.get('sized_down_lots', 1)
        )
        
        # Notifier & Telegram Controller
        self.notifier = Notifier(
            telegram_token=config['credentials'].get('telegram_token'),
            telegram_chat_id=config['credentials'].get('telegram_chat_id'),
            discord_webhook=config['credentials'].get('discord_webhook'),
            credentials=config.get('credentials', {})
        )
        self.telegram = TelegramController(
            token=config['credentials'].get('telegram_token'),
            chat_id=config['credentials'].get('telegram_chat_id'),
            engine=self
        )
        
        # Set Initial Balances
        try:
            self.crypto_balance = 10000.0 if self.is_paper else self.crypto_fetcher.exchange.fetch_balance()['total'].get('USDT', 0.0)
            self.crypto_risk.set_initial_balance(self.crypto_balance)
        except Exception as e:
            logging.error(f"Failed crypto balance fetch: {e}")
            self.crypto_balance = 10000.0
            self.crypto_risk.set_initial_balance(self.crypto_balance)
            
        self.stocks_balance = stocks_cfg.get('initial_capital', 1000000.0)
        self.stocks_risk.set_initial_balance(self.stocks_balance)

    def get_balance_report(self) -> str:
        reports = []
        if self.asset_mode in ("crypto", "both") or len(self.crypto_risk.open_trades) > 0:
            open_risk = sum(t['entry_price'] * t['amount'] for t in self.crypto_risk.open_trades.values())
            reports.append(
                f"Balance Report (CRYPTO):\n"
                f"  Initial Balance: ₹{self.crypto_risk.initial_balance:.2f} USDT\n"
                f"  Current PnL: ₹{self.crypto_risk.daily_pnl:.2f} USDT\n"
                f"  Open Positions Capital: ₹{open_risk:.2f} USDT"
            )
        if self.asset_mode in ("stocks", "both") or len(self.stocks_execution.active_spreads) > 0:
            reports.append(
                f"Balance Report (STOCKS):\n"
                f"  Current Capital: ₹{self.stocks_balance:.2f}\n"
                f"  Locked Collateral: ₹{self.stocks_risk.current_collateral:.2f}\n"
                f"  Available Margin: ₹{self.stocks_balance - self.stocks_risk.current_collateral:.2f}"
            )
        return "\n\n".join(reports)

    def get_positions_report(self) -> str:
        reports = []
        
        crypto_open = len(self.crypto_risk.open_trades) > 0
        if self.asset_mode in ("crypto", "both") or crypto_open:
            if not crypto_open:
                reports.append("Open Crypto Positions:\n- None")
            else:
                report = "Open Crypto Positions:\n"
                for symbol, t in self.crypto_risk.open_trades.items():
                    report += f"- {symbol}: {t['amount']:.4f} @ {t['entry_price']:.2f} (TP: {t['take_profit']:.2f}, SL: {t['stop_loss']:.2f})\n"
                reports.append(report)
                
        stocks_open = len(self.stocks_execution.active_spreads) > 0
        if self.asset_mode in ("stocks", "both") or stocks_open:
            if not stocks_open:
                reports.append("Active Options Spreads:\n- None")
            else:
                report = "Active Options Spreads:\n"
                for symbol, spread in self.stocks_execution.active_spreads.items():
                    contracts = self.stocks_risk.open_spreads.get(symbol, {}).get('contracts', 1)
                    strat = spread.get('strategy_type', 'BULL_PUT')
                    from stocks.options_strategy import format_strategy_legs
                    legs_str = format_strategy_legs(spread, indent="  ")
                    report += (
                        f"- {symbol} ({contracts} contracts - {strat}):\n"
                        f"{legs_str}\n"
                    )
                    report += f"  DTE: {spread['current_dte']} days | Mid Net Credit: ₹{spread['net_credit']:.2f}\n"
                reports.append(report)
                
        return "\n\n".join(reports)

    def get_profits_report(self) -> str:
        from db import load_closed_trades_history
        history = load_closed_trades_history(limit=15)
        if not history:
            return "📈 No closed trades in database history yet."
            
        reports = ["📊 CLOSED TRADES HISTORY & PROFITS REPORT:\n"]
        crypto_total_pnl = 0.0
        stocks_total_pnl = 0.0
        
        for idx, r in enumerate(history, 1):
            pnl = r['pnl']
            pnl_sign = "🟢" if pnl >= 0 else "🔴"
            time_str = r['closed_at'].strftime("%Y-%m-%d %H:%M")
            
            if r['asset_type'] == 'crypto':
                crypto_total_pnl += pnl
                currency = "USDT"
                reports.append(
                    f"{idx}. {pnl_sign} {r['symbol']} (Crypto - {r['direction']})\n"
                    f"   Amount: {r['amount']:.4f} | Entry: {r['entry_price']:.2f} | Exit: {r['exit_price']:.2f}\n"
                    f"   PnL: {pnl_sign}{pnl:.2f} {currency} | Reason: {r['reason']} | Closed: {time_str}"
                )
            else:
                stocks_total_pnl += pnl
                currency = "₹"
                reports.append(
                    f"{idx}. {pnl_sign} {r['symbol']} (Stocks - {r['direction']})\n"
                    f"   Contracts: {int(r['amount'])} | Entry Credit: ₹{r['entry_price']:.2f} | Exit Value: ₹{r['exit_price']:.2f}\n"
                    f"   PnL: {pnl_sign}{currency}{pnl:.2f} | Reason: {r['reason']} | Closed: {time_str}"
                )
        
        summary_lines = ["\n💰 SUMMARY:"]
        if crypto_total_pnl != 0.0 or any(r['asset_type'] == 'crypto' for r in history):
            summary_lines.append(f"  Crypto Net PnL: {'+' if crypto_total_pnl >= 0 else ''}{crypto_total_pnl:.2f} USDT")
        if stocks_total_pnl != 0.0 or any(r['asset_type'] == 'stock_options' for r in history):
            summary_lines.append(f"  Stocks Net PnL: {'+' if stocks_total_pnl >= 0 else ''}₹{stocks_total_pnl:.2f}")
            
        reports.append("\n".join(summary_lines))
        return "\n".join(reports)

    def has_open_positions(self) -> bool:
        return len(self.crypto_risk.open_trades) > 0 or len(self.stocks_execution.active_spreads) > 0

    def close_all_positions(self) -> int:
        count = 0
        # Close crypto open trades
        for symbol in list(self.crypto_risk.open_trades.keys()):
            try:
                if not self.is_paper:
                    ticker = self.crypto_fetcher.exchange.fetch_ticker(symbol)
                    price = ticker['last']
                    self.crypto_execution.execute_market_buy(symbol, -self.crypto_risk.open_trades[symbol]['amount'])
                    self.crypto_fetcher.exchange.cancel_all_orders(symbol)
                else:
                    price = self.crypto_risk.open_trades[symbol]['entry_price']
                self.crypto_risk.close_trade(symbol, price, reason='CLOSEALL')
                count += 1
            except Exception as e:
                logging.error(f"Error closing position: {e}")
                
        # Close stocks option spreads
        for symbol in list(self.stocks_execution.active_spreads.keys()):
            try:
                spread = self.stocks_execution.active_spreads[symbol]
                exit_val = spread['net_credit'] * 0.50
                lot_size = self.stocks_risk.open_spreads[symbol].get('lot_size', self.stocks_risk.lot_size)
                pnl = (spread['net_credit'] - exit_val) * self.stocks_risk.open_spreads[symbol]['contracts'] * lot_size
                self.stocks_balance += pnl
                self.stocks_risk.close_spread(symbol, pnl, reason='CLOSEALL')
                self.stocks_execution.close_spread(symbol)
                count += 1
            except Exception as e:
                logging.error(f"Error closing options spread: {e}")
        return count

    def run(self):
        mode_str = "LIVE" if not self.is_paper else "PAPER"
        
        if not self.is_paper and self.asset_mode in ("crypto", "both"):
            confirmation = input("\nWARNING: You are deploying LIVE capital. Type 'LIVE' exactly to confirm: ")
            if confirmation != 'LIVE':
                print("Aborted. Restart to try again.")
                sys.exit(0)
                
        self.notifier.send_message(f"Bot started | Mode: {mode_str.upper()} | Asset Mode: {self.asset_mode.upper()}")
        
        def graceful_shutdown(signum, frame):
            self.running = False
            logging.info("Graceful shutdown received.")
            self.notifier.send_message("Bot engine transitioning to offline mode gracefully.")

        signal.signal(signal.SIGINT, graceful_shutdown)
        signal.signal(signal.SIGTERM, graceful_shutdown)

        last_strategy_tick = 0.0
        strategy_tick_interval = 15.0

        while self.running:
            self.telegram.poll_commands()
            
            now = time.time()
            if now - last_strategy_tick >= strategy_tick_interval:
                last_strategy_tick = now
                self._check_exits()
                if self.trading_active:
                    self._check_entries()
                    
            time.sleep(1)

    def _check_exits(self):
        # 1. Check Crypto Exits
        if self.asset_mode in ("crypto", "both") or len(self.crypto_risk.open_trades) > 0:
            for symbol in list(self.crypto_risk.open_trades.keys()):
                if not self.is_paper:
                    try:
                        open_orders = self.crypto_fetcher.exchange.fetch_open_orders(symbol)
                        if len(open_orders) == 0:
                            ticker = self.crypto_fetcher.exchange.fetch_ticker(symbol)
                            self.crypto_risk.close_trade(symbol, ticker['last'], reason='TP/SL')
                            self.notifier.send_message(f"Closed trade for {symbol} at {ticker['last']:.2f}")
                    except Exception:
                        pass
                else:
                    try:
                        df = self.crypto_fetcher.fetch_ohlcv(symbol, self.config['trading']['timeframe'], limit=5)
                        if not df.empty:
                            last_price = df.iloc[-1]['close']
                            trade = self.crypto_risk.open_trades[symbol]
                            if last_price >= trade['take_profit']:
                                self.crypto_risk.close_trade(symbol, trade['take_profit'], reason='TP')
                                self.notifier.send_message(f"Crypto TP hit for {symbol} at {trade['take_profit']:.2f}")
                            elif last_price <= trade['stop_loss']:
                                self.crypto_risk.close_trade(symbol, trade['stop_loss'], reason='SL')
                                self.notifier.send_message(f"Crypto SL hit for {symbol} at {trade['stop_loss']:.2f}")
                    except Exception as e:
                        logger.error(f"Error checking crypto paper exit: {e}")

        # 2. Check Stocks Exits
        if self.asset_mode in ("stocks", "both") or len(self.stocks_execution.active_spreads) > 0:
            for symbol, spread in list(self.stocks_execution.active_spreads.items()):
                # Fetch option chain (puts and calls)
                _, puts, calls, current_price = self.stocks_fetcher.fetch_option_chain(symbol, target_dte=spread['dte'])
                if puts.empty and calls.empty:
                    continue
                    
                strat = spread.get('strategy_type', 'BULL_PUT')
                if 'current_dte' not in spread:
                    spread['current_dte'] = spread.get('dte', 30)
                
                # We calculate current_spread_val and current_dte
                if strat in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
                    opt = calls[calls['strike'] == spread['long_strike']]
                    if not opt.empty:
                        current_spread_val = opt.iloc[0]['mid']
                        spread['current_dte'] = int(opt.iloc[0]['dte'])
                    else:
                        current_spread_val = max(0.0, abs(spread['net_credit']) * (spread['current_dte'] / spread['dte']))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                elif strat in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
                    opt = puts[puts['strike'] == spread['long_strike']]
                    if not opt.empty:
                        current_spread_val = opt.iloc[0]['mid']
                        spread['current_dte'] = int(opt.iloc[0]['dte'])
                    else:
                        current_spread_val = max(0.0, abs(spread['net_credit']) * (spread['current_dte'] / spread['dte']))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                elif strat == 'CASH_SECURED_PUT':
                    opt = puts[puts['strike'] == spread['short_strike']]
                    if not opt.empty:
                        current_spread_val = opt.iloc[0]['mid']
                        spread['current_dte'] = int(opt.iloc[0]['dte'])
                    else:
                        current_spread_val = max(0.0, spread['net_credit'] * (spread['current_dte'] / spread['dte']))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                elif strat == 'COVERED_CALL':
                    opt = calls[calls['strike'] == spread['short_strike']]
                    if not opt.empty:
                        current_spread_val = current_price - opt.iloc[0]['mid']
                        spread['current_dte'] = int(opt.iloc[0]['dte'])
                    else:
                        current_spread_val = current_price - max(0.0, spread['net_credit'] * (spread['current_dte'] / spread['dte']))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                elif strat == 'CALENDAR_SPREAD':
                    short_opt = calls[calls['strike'] == spread['short_strike']]
                    if not short_opt.empty:
                        near_val = short_opt.iloc[0]['mid']
                        T_long = (spread['current_dte'] + 30) / 365.0
                        sigma = short_opt.iloc[0].get('impliedVolatility', 0.25)
                        long_val = self.stocks_execution._bs_call_price(current_price, spread['short_strike'], T_long, sigma)
                        current_spread_val = long_val - near_val
                        spread['current_dte'] = int(short_opt.iloc[0]['dte'])
                    else:
                        current_spread_val = max(0.0, abs(spread['net_credit']) * (spread['current_dte'] / spread['dte']))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                elif strat == 'BULL_PUT':
                    short_opt = puts[puts['strike'] == spread['short_strike']]
                    long_opt = puts[puts['strike'] == spread['long_strike']]
                    if not short_opt.empty and not long_opt.empty:
                        current_spread_val = short_opt.iloc[0]['mid'] - long_opt.iloc[0]['mid']
                        spread['current_dte'] = int(short_opt.iloc[0]['dte'])
                    else:
                        dist = current_price - spread['underlying_price']
                        current_spread_val = max(0.0, min(spread['spread_width'], spread['net_credit'] - (dist * 0.1)))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                elif strat == 'BEAR_CALL':
                    short_opt = calls[calls['strike'] == spread['short_strike']]
                    long_opt = calls[calls['strike'] == spread['long_strike']]
                    if not short_opt.empty and not long_opt.empty:
                        current_spread_val = short_opt.iloc[0]['mid'] - long_opt.iloc[0]['mid']
                        spread['current_dte'] = int(short_opt.iloc[0]['dte'])
                    else:
                        dist = spread['underlying_price'] - current_price
                        current_spread_val = max(0.0, min(spread['spread_width'], spread['net_credit'] - (dist * 0.1)))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                elif strat == 'IRON_BUTTERFLY':
                    short_put = puts[puts['strike'] == spread['short_put_strike']]
                    long_put = puts[puts['strike'] == spread['long_put_strike']]
                    short_call = calls[calls['strike'] == spread['short_call_strike']]
                    long_call = calls[calls['strike'] == spread['long_call_strike']]
                    
                    if not short_put.empty and not long_put.empty and not short_call.empty and not long_call.empty:
                        put_val = short_put.iloc[0]['mid'] - long_put.iloc[0]['mid']
                        call_val = short_call.iloc[0]['mid'] - long_call.iloc[0]['mid']
                        current_spread_val = put_val + call_val
                        spread['current_dte'] = int(short_put.iloc[0]['dte'])
                    else:
                        current_spread_val = max(0.0, spread['net_credit'] * (spread['current_dte'] / spread['dte']))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                else:  # IRON_CONDOR
                    short_put = puts[puts['strike'] == spread.get('short_put_strike', 0.0)]
                    long_put = puts[puts['strike'] == spread.get('long_put_strike', 0.0)]
                    short_call = calls[calls['strike'] == spread.get('short_call_strike', 0.0)]
                    long_call = calls[calls['strike'] == spread.get('long_call_strike', 0.0)]
                    
                    if not short_put.empty and not long_put.empty and not short_call.empty and not long_call.empty:
                        put_val = short_put.iloc[0]['mid'] - long_put.iloc[0]['mid']
                        call_val = short_call.iloc[0]['mid'] - long_call.iloc[0]['mid']
                        current_spread_val = put_val + call_val
                        spread['current_dte'] = int(short_put.iloc[0]['dte'])
                    else:
                        # Fallback decay calculation
                        current_spread_val = max(0.0, spread['net_credit'] * (spread['current_dte'] / spread['dte']))
                        spread['current_dte'] = max(0, spread['current_dte'] - 1)
                        
                # Update database with new DTE and info before checking exit
                try:
                    if symbol in self.stocks_risk.open_spreads:
                        contracts = self.stocks_risk.open_spreads[symbol]['contracts']
                        margin_lockup = self.stocks_risk.open_spreads[symbol]['margin_lockup']
                        save_stock_spread(symbol, spread, contracts, margin_lockup)
                except Exception as e:
                    logging.error(f"Failed to update options spread database state: {e}")

                closed_position = self.stocks_execution.check_exit_conditions(
                    symbol=symbol,
                    current_underlying_price=current_price,
                    current_spread_value=current_spread_val,
                    profit_target_pct=self.config['stocks'].get('profit_target_pct', 0.40),
                    stop_loss_mult=self.config['stocks'].get('stop_loss_mult', 2.0)
                )
                
                if closed_position:
                    lot_size = self.stocks_risk.open_spreads[symbol].get('lot_size', self.stocks_risk.lot_size)
                    real_pnl = closed_position['pnl'] * self.stocks_risk.open_spreads[symbol]['contracts'] * lot_size
                    self.stocks_balance += real_pnl
                    self.stocks_risk.close_spread(symbol, real_pnl, reason=closed_position['reason'])
                    self.notifier.send_message(f"💰 Options Closed for {symbol}: {closed_position['reason']} | PnL: ₹{real_pnl:.2f} | Balance: ₹{self.stocks_balance:.2f}")

    def _check_entries(self):
        # 1. Check Crypto Entries
        if self.asset_mode in ("crypto", "both"):
            symbols = self.config['trading'].get('symbols', [])
            if not symbols or (len(symbols) == 1 and symbols[0].lower() == 'all'):
                try:
                    self.crypto_fetcher.exchange.load_markets()
                    symbols = [
                        sym for sym, market in self.crypto_fetcher.exchange.markets.items()
                        if market.get('active') and sym.endswith('/USDT')
                    ]
                except Exception as e:
                    logging.error(f"Error loading all ccxt cryptos: {e}. Falling back to default list.")
                    symbols = ['BTC/USDT', 'ETH/USDT']
                    
            timeframe = self.config['trading']['timeframe']
            for symbol in symbols:
                if symbol in self.crypto_risk.open_trades:
                    continue
                if not self.crypto_risk.can_open_trade():
                    continue
                df = self.crypto_fetcher.fetch_ohlcv(symbol, timeframe, limit=300)
                if df.empty:
                    continue
                
                df_signals = self.crypto_strategy.generate_signals(df)
                if df_signals.empty:
                    continue
                
                last_row = df_signals.iloc[-1]
                
                # Check if indicators are successfully computed (avoid KeyError if not enough history)
                if 'rsi' not in last_row:
                    logging.warning(f"Could not compute indicators for {symbol}. Data contains {len(df_signals)} rows, but strategy requires {self.crypto_strategy.ema_long_period} rows.")
                    continue
                
                # Diagnostic scan logging
                logging.info(
                    f"Scan {symbol} ({timeframe}) | Close: ₹{last_row['close']:.2f} | "
                    f"RSI: {last_row['rsi']:.1f}/{self.crypto_strategy.rsi_oversold} | "
                    f"EMA50 dist: {abs(last_row['close'] - last_row['ema50'])/last_row['ema50']*100:.2f}% (max {self.crypto_strategy.ema_proximity_pct*100}%) | "
                    f"Above EMA200: {last_row['close'] > last_row['ema200']} | "
                    f"MACD bullish: {last_row['macd'] > last_row['macd_signal']} | "
                    f"Vol Spike: {last_row.get('volume_spike', False)}"
                )
                
                if bool(last_row.get('buy_signal', False)):
                     current_price = last_row['close']
                     
                     # LLM Brain Verification
                     if self.brain_enabled:
                         logging.info(f"🧠 Querying TradingBrain to verify strategy BUY signal for {symbol}...")
                         brain_result = self.crypto_brain.analyze_market(symbol, timeframe, df)
                         decision = brain_result.get("decision", "APPROVE")
                         rationale = brain_result.get("rationale", "")
                         confidence = brain_result.get("confidence", 1.0)
                         
                         if decision == "VETO" and self.veto_power:
                             logging.info(f"🛑 Trade entry for {symbol} VETOED by TradingBrain (Confidence: {confidence:.2f}). Rationale: {rationale}")
                             continue
                         else:
                             logging.info(f"✅ Trade entry for {symbol} APPROVED by TradingBrain (Confidence: {confidence:.2f}). Rationale: {rationale}")
                     else:
                         rationale = "Strategy conditions satisfied."
                         
                     amount = self.crypto_risk.calculate_position_size(self.crypto_balance, current_price)
                     order = self.crypto_execution.execute_market_buy(symbol, amount)
                     if order:
                         entry_price = order.get('price') or current_price
                         atr_val = df.iloc[-1].get('atr') if 'atr' in df.columns else None
                         self.crypto_risk.register_trade(symbol, amount, entry_price, atr=atr_val)
                         tp, sl = self.crypto_risk.calculate_tp_sl(entry_price, atr=atr_val)
                         self.crypto_execution.execute_limit_sell(symbol, amount, tp)
                         self.crypto_execution.execute_stop_market_sell(symbol, amount, sl)
                         
                         # Generate natural language commentary
                         if self.brain_enabled:
                             commentary = self.crypto_brain.explain_trade(
                                 symbol=symbol,
                                 action="BUY",
                                 entry=entry_price,
                                 tp=tp,
                                 sl=sl,
                                 rationale=rationale
                             )
                         else:
                             commentary = f"🚀 BUY {symbol} @ {entry_price:.2f} | TP: {tp:.2f} | SL: {sl:.2f}"
                             
                         self.notifier.send_message(commentary)

        # 2. Check Stocks Entries
        if self.asset_mode in ("stocks", "both"):
            cfg_symbols = self.config['stocks'].get('symbols', [])
            if not cfg_symbols or (len(cfg_symbols) == 1 and cfg_symbols[0].lower() == 'all'):
                try:
                    cfg_symbols = self.stocks_fetcher.fetch_all_nse_fo_symbols()
                except Exception as e:
                    logging.error(f"Error fetching dynamic NSE symbols: {e}")
                    cfg_symbols = ['RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS']
                    
            symbols = [s for s in cfg_symbols if s not in self.stocks_execution.active_spreads]
            for symbol in symbols:
                if not self.stocks_risk.can_open_spread():
                    break
                spread_info = self.stocks_strategy.scan_for_spread(symbol)
                if spread_info:
                    # LLM Brain Verification for Options
                    rationale = "Options strategy entry criteria met."
                    if self.brain_enabled:
                        logging.info(f"🧠 Querying TradingBrain to verify options spread signal for {symbol}...")
                        try:
                            hist_df = self.stocks_fetcher.fetch_stock_history(symbol, period="1y")
                            brain_result = self.crypto_brain.analyze_options_spread(symbol, spread_info, hist_df)
                            decision = brain_result.get("decision", "APPROVE")
                            rationale = brain_result.get("rationale", "")
                            confidence = brain_result.get("confidence", 1.0)
                            
                            if decision == "VETO" and self.veto_power:
                                logging.info(f"🛑 Options trade for {symbol} VETOED by TradingBrain (Confidence: {confidence:.2f}). Rationale: {rationale}")
                                continue
                            else:
                                logging.info(f"✅ Options trade for {symbol} APPROVED by TradingBrain (Confidence: {confidence:.2f}). Rationale: {rationale}")
                        except Exception as e:
                            logging.error(f"Error calling brain options analysis for {symbol}: {e}")
                    
                    contracts = self.stocks_risk.calculate_contracts(self.stocks_balance, spread_info['spread_width'], symbol)
                    if contracts > 0:
                        lot_size = get_lot_size(symbol, self.stocks_risk.lot_size)
                        margin_required = spread_info['spread_width'] * lot_size * contracts
                        available_margin = self.stocks_balance - self.stocks_risk.current_collateral
                        if available_margin >= margin_required:
                            if self.stocks_execution.execute_sell_spread(spread_info):
                                self.stocks_risk.register_spread(symbol, spread_info, contracts)
                                # Generate natural language commentary
                                if self.brain_enabled:
                                    try:
                                        msg = self.crypto_brain.explain_options_spread(
                                            symbol=symbol,
                                            spread_info=spread_info,
                                            contracts=contracts,
                                            rationale=rationale
                                        )
                                    except Exception as e:
                                        logging.error(f"Failed to generate brain options explanation: {e}")
                                        from stocks.options_strategy import format_strategy_legs
                                        legs_str = format_strategy_legs(spread_info, indent="   ")
                                        msg = (
                                            f"🚀 Entered options spread on {symbol} ({spread_info.get('strategy_type', 'BULL_PUT')}):\n"
                                            f"{legs_str}\n"
                                            f"   Net Credit/Debit: ₹{spread_info['net_credit']:.2f} | Contracts: {contracts}"
                                        )
                                else:
                                    strat = spread_info.get('strategy_type', 'BULL_PUT')
                                    from stocks.options_strategy import format_strategy_legs
                                    legs_str = format_strategy_legs(spread_info, indent="   ")
                                    emoji = "🦅" if strat in ("IRON_CONDOR", "IRON_BUTTERFLY") else "🚀"
                                    msg = (
                                        f"{emoji} Entered options spread on {symbol} ({strat}):\n"
                                        f"{legs_str}\n"
                                        f"   Net Credit/Debit: ₹{spread_info['net_credit']:.2f} | Contracts: {contracts}"
                                    )
                                self.notifier.send_message(msg)

def run_trade(config):
    engine = TradingBotEngine(config, asset_mode='crypto')
    engine.run()

def run_backtest(config):
    trade_cfg = config.get('trading', {})
    strat_cfg = config.get('strategy', {})
    risk_cfg = config.get('risk', {})
    
    fetcher = DataFetcher(trade_cfg.get('exchange', 'bybit'), '', '', paper=True, api_url=trade_cfg.get('api_url'))
    strategy = SwingStrategy(
        strat_cfg.get('rsi_period', 14), 
        strat_cfg.get('rsi_oversold', 35.0), 
        strat_cfg.get('ema_period', 50), 
        strat_cfg.get('ema_proximity_pct', 0.015),
        vwap_period=strat_cfg.get('vwap_period', 14),
        require_volume_spike=strat_cfg.get('require_volume_spike', True),
        require_macd=strat_cfg.get('require_macd', True),
        require_ema200=strat_cfg.get('require_ema200', True)
    )
    risk = RiskManager(
        risk_cfg.get('take_profit_pct', 0.05), 
        risk_cfg.get('stop_loss_pct', 0.025), 
        risk_cfg.get('max_capital_per_trade_pct', 0.15), 
        risk_cfg.get('daily_loss_limit_pct', 0.10), 
        risk_cfg.get('max_open_trades', 2),
        tp_atr_mult=risk_cfg.get('tp_atr_mult', 3.0),
        sl_atr_mult=risk_cfg.get('sl_atr_mult', 1.5),
        adaptive_sizing=risk_cfg.get('adaptive_sizing', False),
        base_lots=risk_cfg.get('base_lots', 5),
        sized_down_lots=risk_cfg.get('sized_down_lots', 1)
    )
    
    hist_data = {}
    backtest_limit = trade_cfg.get('backtest_limit', 5000)
    cfg_symbols = trade_cfg.get('symbols', ['BTC/USDT', 'ETH/USDT'])
    if not cfg_symbols or (len(cfg_symbols) == 1 and cfg_symbols[0].lower() == 'all'):
        cfg_symbols = ['BTC/USDT', 'ETH/USDT']
    for symbol in cfg_symbols:
        logging.info(f"Downloading historicals for {symbol}...")
        hist_data[symbol] = fetcher.fetch_ohlcv(symbol, trade_cfg.get('timeframe', '15m'), limit=backtest_limit)
        
    backtester = Backtester(strategy, risk)
    results = backtester.run(hist_data)
    
    print("\n--- Backtest Output ---")
    print(f"Total Trades : {results['total_trades']}")
    print(f"Win Rate     : {results['win_rate'] * 100:.2f}%")
    print(f"Profit Factor: {results['profit_factor']:.2f}")
    print(f"Max Drawdown : {results['max_drawdown'] * 100:.2f}%")
    print(f"Final Capital: ₹{results['final_capital']:.2f}\n")

def run_status(config):
    print("[BOT] System Core Configuration:")
    for section, values in config.items():
        print(f"[{section.upper()}]")
        for k, v in values.items():
            if 'secret' in k or 'key' in k or 'token' in k: v = "******"
            print(f"  {k}: {v}")
    print("\nReady to roll.")

def run_stocks_trade(config):
    engine = TradingBotEngine(config, asset_mode='stocks')
    engine.run()

def run_stocks_backtest(config):
    logging.info("Initializing Stock Options Backtester...")
    stocks_cfg = config.get('stocks', {})
    if not stocks_cfg:
        logging.error("No stocks config found.")
        sys.exit(1)
        
    fetcher = StockDataFetcher()
    backtester = StocksOptionsBacktester(
        target_dte=stocks_cfg.get('target_dte', 30),
        target_delta=stocks_cfg.get('target_delta', -0.25),
        spread_width=stocks_cfg.get('spread_width', 100.0),
        min_iv_rank=stocks_cfg.get('min_iv_rank', 15.0),
        profit_target_pct=stocks_cfg.get('profit_target_pct', 0.40),
        stop_loss_mult=stocks_cfg.get('stop_loss_mult', 2.0),
        max_capital_per_spread_pct=stocks_cfg.get('max_capital_per_spread_pct', 0.40),
        initial_capital=stocks_cfg.get('initial_capital', 1000000.0),
        lot_size=stocks_cfg.get('lot_size', 100.0),
        base_lots=stocks_cfg.get('base_lots', 5),
        sized_down_lots=stocks_cfg.get('sized_down_lots', 1)
    )
    
    hist_data = {}
    cfg_symbols = stocks_cfg.get('symbols', ['SPY', 'QQQ', 'AAPL', 'MSFT'])
    if not cfg_symbols or (len(cfg_symbols) == 1 and cfg_symbols[0].lower() == 'all'):
        cfg_symbols = [
            'RELIANCE.NS', 'TCS.NS', 'INFY.NS', 'HDFCBANK.NS', 'ICICIBANK.NS',
            'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'LT.NS', 'AXISBANK.NS',
            'KOTAKBANK.NS', 'HINDUNILVR.NS'
        ]
    for symbol in cfg_symbols:
        logging.info(f"Downloading historical daily data for {symbol}...")
        hist_data[symbol] = fetcher.fetch_stock_history(symbol, period="2y")
        
    results = backtester.run_backtest(hist_data)
    
    print("\n--- Stock Options Backtest Output ---")
    print(f"Total Trades : {results['total_trades']}")
    print(f"Win Rate     : {results['win_rate'] * 100:.2f}%")
    print(f"Profit Factor: {results['profit_factor']:.2f}")
    print(f"Max Drawdown : {results['max_drawdown'] * 100:.2f}%")
    print(f"Final Capital: ₹{results['final_capital']:.2f}\n")

def start_health_server():
    import os
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    
    port = int(os.environ.get('PORT', 8080))
    
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ('/health', '/'):
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
                self.end_headers()
                
        def log_message(self, format, *args):
            pass  # Suppress normal HTTP logging
            
    def run_server():
        try:
            server = HTTPServer(('0.0.0.0', port), HealthHandler)
            logging.info(f"Health check web server listening on port {port}.")
            server.serve_forever()
        except Exception as e:
            logging.error(f"Failed to start health web server: {e}")
            
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

def main():
    parser = argparse.ArgumentParser(description="Automated Crypto Swing & Stock Options Trader")
    parser.add_argument('--config', type=str, default='config.yaml', help='Path to configuration file')
    parser.add_argument('--mode', type=str, default='crypto', choices=['crypto', 'stocks', 'both'], help='Asset mode: crypto, stocks, or both')
    
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('trade', help='Run the production trading bot instance')
    subparsers.add_parser('backtest', help='Execute historical strategy simulation')
    subparsers.add_parser('status', help='Dump current active configuration variables')

    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Failed to load config: {e}")
        return
        
    setup_logging(config)
    try:
        init_db()
    except Exception as e:
        print(f"CRITICAL: Failed to initialize database: {e}")
        return

    if args.command == 'trade':
        start_health_server()
        if args.mode == 'stocks':
            run_stocks_trade(config)
        elif args.mode == 'both':
            engine = TradingBotEngine(config, asset_mode='both')
            engine.run()
        else:
            run_trade(config)
    elif args.command == 'backtest':
        if args.mode == 'stocks':
            run_stocks_backtest(config)
        else:
            run_backtest(config)
    elif args.command == 'status':
        run_status(config)

if __name__ == "__main__":
    main()

