import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self, 
                 telegram_token: Optional[str] = None, 
                 telegram_chat_id: Optional[str] = None, 
                 discord_webhook: Optional[str] = None,
                 credentials: Optional[dict] = None):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.discord_webhook = discord_webhook
        self.credentials = credentials or {}

    def send_message(self, message: str):
        from security_utils import redact_credentials
        redacted_msg = redact_credentials(message, self.credentials)
        logger.info(redacted_msg)
        self._send_telegram(redacted_msg)
        self._send_discord(redacted_msg)
        self._send_whatsapp(redacted_msg)

    def _send_telegram(self, message: str):
        if not self.telegram_token or not self.telegram_chat_id: return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, json={"chat_id": self.telegram_chat_id, "text": message}, timeout=5)
        except Exception as e:
            logger.error(f"Telegram failed: {e}")

    def _send_discord(self, message: str):
        if not self.discord_webhook: return
        try:
            requests.post(self.discord_webhook, json={"content": message}, timeout=5)
        except Exception as e:
            logger.error(f"Discord failed: {e}")

    def _send_whatsapp(self, message: str):
        sid = self.credentials.get('whatsapp_twilio_sid')
        token = self.credentials.get('whatsapp_twilio_token')
        sender = self.credentials.get('whatsapp_sender_number')
        admin = self.credentials.get('whatsapp_admin_number')

        if not sid or not token or not sender or not admin:
            return

        sender_num = sender.strip()
        if not sender_num.startswith("whatsapp:"):
            sender_num = f"whatsapp:{sender_num}"

        admin_num = admin.strip()
        if not admin_num.startswith("whatsapp:"):
            admin_num = f"whatsapp:{admin_num}"

        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            payload = {
                "From": sender_num,
                "To": admin_num,
                "Body": message
            }
            requests.post(url, data=payload, auth=(sid, token), timeout=10)
        except Exception as e:
            logger.error(f"WhatsApp notification failed: {e}")
