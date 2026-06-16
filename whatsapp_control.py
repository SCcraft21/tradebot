import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class WhatsAppController:
    def __init__(self, twilio_sid: Optional[str], twilio_token: Optional[str], 
                 sender_number: Optional[str], admin_number: Optional[str], engine):
        self.twilio_sid = twilio_sid
        self.twilio_token = twilio_token
        
        # Normalize and ensure 'whatsapp:' prefix for numbers
        self.sender_number = sender_number.strip() if sender_number else None
        if self.sender_number and not self.sender_number.startswith("whatsapp:"):
            self.sender_number = f"whatsapp:{self.sender_number}"
            
        self.admin_number = admin_number.strip() if admin_number else None
        if self.admin_number and not self.admin_number.startswith("whatsapp:"):
            self.admin_number = f"whatsapp:{self.admin_number}"
            
        self.engine = engine
        
        if not self.twilio_sid or not self.twilio_token or not self.sender_number or not self.admin_number:
            logger.info("WhatsApp control feature is disabled (Twilio credentials or numbers missing in config).")
            self.enabled = False
        else:
            logger.info(f"WhatsApp control feature initialized. Listening for Admin Number: {self.admin_number}")
            self.enabled = True

    def send_message(self, text: str, recipient: str = None):
        """Send a WhatsApp message via Twilio API."""
        if not self.enabled:
            return
            
        to_number = recipient or self.admin_number
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_sid}/Messages.json"
            payload = {
                "From": self.sender_number,
                "To": to_number,
                "Body": text
            }
            res = requests.post(url, data=payload, auth=(self.twilio_sid, self.twilio_token), timeout=10)
            if res.status_code not in (200, 201):
                logger.error(f"WhatsApp API: Failed to send message. Code: {res.status_code}, Response: {res.text}")
        except Exception as e:
            logger.error(f"WhatsApp API: Error sending message: {e}")

    def process_message(self, sender: str, body: str) -> bool:
        """
        Process an incoming WhatsApp message.
        Returns True if processed successfully, False otherwise.
        """
        if not self.enabled:
            return False
            
        sender = sender.strip()
        if not sender.startswith("whatsapp:"):
            sender = f"whatsapp:{sender}"
            
        # Security Check
        if sender != self.admin_number:
            logger.warning(f"WhatsApp API: Unauthorized message attempt from {sender}.")
            self.send_message("Unauthorized! You are not the administrator of this bot.", recipient=sender)
            return False
            
        body_text = body.strip()
        if not body_text:
            return False
            
        logger.info(f"WhatsApp API: Received command from admin: '{body_text}'")
        self._handle_command(body_text, sender)
        return True

    def _handle_command(self, cmd_text: str, sender: str):
        parts = cmd_text.split()
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1:]
        
        # Command routing (support both slash prefixed and plain commands)
        if cmd in ("/help", "help"):
            help_text = (
                "📱 WhatsApp Trading Bot Control Commands:\n\n"
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
            self.send_message(help_text, recipient=sender)
            
        elif cmd in ("/status", "status"):
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
            self.send_message(status_text, recipient=sender)
            
        elif cmd in ("/balance", "balance"):
            bal_text = self.engine.get_balance_report()
            self.send_message(bal_text, recipient=sender)
            
        elif cmd in ("/positions", "positions"):
            pos_text = self.engine.get_positions_report()
            self.send_message(pos_text, recipient=sender)
            
        elif cmd in ("/profits", "profits"):
            prof_text = self.engine.get_profits_report()
            self.send_message(prof_text, recipient=sender)
            
        elif cmd in ("/switch", "switch"):
            old_mode = self.engine.asset_mode
            target_mode = None
            if args:
                requested = args[0].lower()
                if requested in ("crypto", "stocks", "both"):
                    target_mode = requested
                else:
                    self.send_message("⚠️ Invalid mode. Use: /switch [crypto | stocks | both]", recipient=sender)
                    return
            else:
                if old_mode == "crypto":
                    target_mode = "stocks"
                elif old_mode == "stocks":
                    target_mode = "both"
                else:
                    target_mode = "crypto"
            
            self.engine.asset_mode = target_mode
            self.send_message(f"🔄 Mode switched successfully!\nOld Mode: {old_mode.upper()}\nNew Active Mode: {target_mode.upper()}", recipient=sender)
            logger.info(f"WhatsApp control dynamically switched asset mode from {old_mode} to {target_mode}")
                
        elif cmd in ("/pause", "pause"):
            if not self.engine.trading_active:
                self.send_message("Bot is already paused.", recipient=sender)
            else:
                self.engine.trading_active = False
                self.send_message("⏸️ Trading PAUSED. Bot will not open new positions, but will continue to monitor exits on open positions.", recipient=sender)
                logger.info("WhatsApp control PAUSED trading.")
                
        elif cmd in ("/resume", "resume"):
            if self.engine.trading_active:
                self.send_message("Bot is already active.", recipient=sender)
            else:
                self.engine.trading_active = True
                self.send_message("▶️ Trading RESUMED. Bot is scanning for new entry opportunities.", recipient=sender)
                logger.info("WhatsApp control RESUMED trading.")
                
        elif cmd in ("/closeall", "closeall"):
            self.send_message("🚨 Liquidating all open positions...", recipient=sender)
            count = self.engine.close_all_positions()
            self.send_message(f"✅ Liquidation complete. Closed {count} positions.", recipient=sender)
            
        elif cmd in ("/shutdown", "shutdown", "/stop", "stop"):
            self.send_message("🛑 Shutting down trading engine and daemon runner...", recipient=sender)
            logger.info("WhatsApp control shutdown command received.")
            self.engine.running = False
            
        else:
            self.send_message("Unknown command. Type help or /help to see all available commands.", recipient=sender)
