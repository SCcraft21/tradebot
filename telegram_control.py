import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class TelegramController:
    def __init__(self, token: Optional[str], chat_id: Optional[str], engine):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.engine = engine  # Reference to the main bot execution loop / state
        self.offset = 0
        
        if not self.token or not self.chat_id:
            logger.info("Telegram control feature is disabled (token or chat_id is missing in config).")
        else:
            logger.info(f"Telegram control feature initialized. Listening to Admin Chat ID: {self.chat_id}")
            try:
                url = f"https://api.telegram.org/bot{self.token}/getUpdates"
                params = {"offset": -1, "limit": 1, "timeout": 0}
                res = requests.get(url, params=params, timeout=5).json()
                if res.get("ok") and res.get("result"):
                    self.offset = res["result"][0]["update_id"] + 1
                    logger.info(f"Acknowledged previous Telegram updates. Starting poll from offset: {self.offset}")
            except Exception as e:
                logger.warning(f"Failed to clear old Telegram updates on startup: {e}")

    def send_message(self, text: str):
        """Send message back to the Admin chat."""
        if not self.token or not self.chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")

    def poll_commands(self):
        """Poll getUpdates from Telegram and execute commands if received."""
        if not self.token or not self.chat_id:
            return
            
        try:
            url = f"https://api.telegram.org/bot{self.token}/getUpdates"
            params = {"offset": self.offset, "timeout": 0}
            response = requests.get(url, params=params, timeout=5)
            
            if response.status_code != 200:
                return
                
            data = response.json()
            if not data.get("ok"):
                return
                
            updates = data.get("result", [])
            for update in updates:
                self.offset = update["update_id"] + 1
                
                message = update.get("message")
                if not message:
                    continue
                    
                sender_chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "").strip()
                
                if not text:
                    continue
                    
                # Security Check: Only respond to the configured admin chat ID
                if not self.chat_id or sender_chat_id != self.chat_id:
                    logger.warning(f"Unauthorized command attempt from chat ID {sender_chat_id}.")
                    # Send warning back to the unauthorized user
                    try:
                        warn_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                        requests.post(warn_url, json={"chat_id": sender_chat_id, "text": "Unauthorized! You are not the administrator of this bot."}, timeout=5)
                    except Exception:
                        pass
                    continue
                
                # Command handler
                if text.startswith("/"):
                    self._handle_command(text)
                    
        except Exception as e:
            # Silence standard network timeout errors to avoid log spamming
            logger.debug(f"Telegram polling exception: {e}")

    def _handle_command(self, cmd_text: str):
        logger.info(f"Received Telegram control command: '{cmd_text}'")
        
        parts = cmd_text.split()
        cmd = parts[0].lower()
        args = parts[1:]
        
        if cmd == "/help":
            help_text = (
                "🤖 Trading Bot Control Commands:\n\n"
                "/status - Show bot active state and mode\n"
                "/balance - Display current capital and collateral\n"
                "/positions - List active trades/option spreads\n"
                "/profits - Display closed trades history and profit/loss report\n"
                "/switch [crypto|stocks|both] - Switch asset mode dynamically\n"
                "/pause - Pause new entries (continues exits)\n"
                "/resume - Resume scanning for new positions\n"
                "/closeall - Liquidate all active positions immediately\n"
                "/shutdown - Terminate trading bot & background daemon\n"
                "/help - View this message"
            )
            self.send_message(help_text)
            
        elif cmd == "/status":
            state = "ACTIVE" if self.engine.trading_active else "PAUSED"
            mode = self.engine.asset_mode.upper()
            config_info = []
            
            if self.engine.asset_mode in ("crypto", "both"):
                config_info.append(f"Crypto Pairs: {', '.join(self.engine.config['trading']['symbols'])}\nTimeframe: {self.engine.config['trading']['timeframe']}")
            if self.engine.asset_mode in ("stocks", "both"):
                config_info.append(f"Stocks: {', '.join(self.engine.config['stocks']['symbols'])}\nTarget DTE: {self.engine.config['stocks']['target_dte']}")
                
            status_text = (
                f"⚙️ BOT STATUS REPORT:\n\n"
                f"Status: {state}\n"
                f"Active Mode: {mode}\n\n"
                f"{chr(10).join(config_info)}"
            )
            self.send_message(status_text)
            
        elif cmd == "/balance":
            bal_text = self.engine.get_balance_report()
            self.send_message(bal_text)
            
        elif cmd == "/positions":
            pos_text = self.engine.get_positions_report()
            self.send_message(pos_text)
            
        elif cmd == "/profits":
            prof_text = self.engine.get_profits_report()
            self.send_message(prof_text)
            
        elif cmd == "/switch":
            old_mode = self.engine.asset_mode
            target_mode = None
            if args:
                requested = args[0].lower()
                if requested in ("crypto", "stocks", "both"):
                    target_mode = requested
                else:
                    self.send_message("⚠️ Invalid mode. Use: /switch [crypto | stocks | both]")
                    return
            else:
                # Cycle mode: crypto -> stocks -> both -> crypto
                if old_mode == "crypto":
                    target_mode = "stocks"
                elif old_mode == "stocks":
                    target_mode = "both"
                else:
                    target_mode = "crypto"
            
            self.engine.asset_mode = target_mode
            self.send_message(f"🔄 Mode switched successfully!\nOld Mode: {old_mode.upper()}\nNew Active Mode: {target_mode.upper()}")
            logger.info(f"Telegram control dynamically switched asset mode from {old_mode} to {target_mode}")
                
        elif cmd == "/pause":
            if not self.engine.trading_active:
                self.send_message("Bot is already paused.")
            else:
                self.engine.trading_active = False
                self.send_message("⏸️ Trading PAUSED. Bot will not open new positions, but will continue to monitor exits on open positions.")
                logger.info("Telegram control PAUSED trading.")
                
        elif cmd == "/resume":
            if self.engine.trading_active:
                self.send_message("Bot is already active.")
            else:
                self.engine.trading_active = True
                self.send_message("▶️ Trading RESUMED. Bot is scanning for new entry opportunities.")
                logger.info("Telegram control RESUMED trading.")
                
        elif cmd == "/closeall":
            self.send_message("🚨 Liquidating all open positions...")
            count = self.engine.close_all_positions()
            self.send_message(f"✅ Liquidation complete. Closed {count} positions.")
            
        elif cmd == "/shutdown" or cmd == "/stop":
            self.send_message("🛑 Shutting down trading engine and daemon runner...")
            logger.info("Telegram control shutdown command received.")
            self.engine.running = False
            
        else:
            self.send_message("Unknown command. Type /help to see all available commands.")
