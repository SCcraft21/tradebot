import sys
import yaml
import requests
import logging

# Set up logging to console
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def load_config(path='config.yaml'):
    try:
        with open(path, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Failed to load config file: {e}")
        return None

def test_telegram(token, chat_id):
    if not token or not chat_id:
        logging.warning("Telegram token or chat ID is missing. Skipping Telegram test.")
        return False
        
    logging.info(f"Testing Telegram Bot connection... Sending to Chat ID: {chat_id}")
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        # Check if token is valid
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            logging.error(f"Telegram Bot Token validation failed (HTTP status {r.status_code}): {r.text}")
            return False
            
        bot_info = r.json()
        bot_name = bot_info.get("result", {}).get("first_name", "Unknown Bot")
        logging.info(f"Successfully authenticated as Bot: '{bot_name}'")
        
        # Send message
        msg_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": "🔔 *Trading Bot Alert*\n\nIf you are reading this message, your Telegram notifications and Chat ID are configured correctly! bot is ready to send updates.",
            "parse_mode": "Markdown"
        }
        r_msg = requests.post(msg_url, json=payload, timeout=5)
        if r_msg.status_code == 200:
            logging.info("Telegram message sent successfully! Check your Telegram chat.")
            return True
        else:
            logging.error(f"Failed to send Telegram message (HTTP status {r_msg.status_code}): {r_msg.text}")
            logging.error("Tip: Make sure you have started a chat with your bot by clicking '/start' inside Telegram before running this.")
            return False
    except Exception as e:
        logging.error(f"Telegram connection exception occurred: {e}")
        return False

def test_discord(webhook_url):
    if not webhook_url:
        logging.warning("Discord webhook URL is missing. Skipping Discord test.")
        return False
        
    logging.info("Testing Discord webhook connection...")
    payload = {
        "content": "🔔 **Trading Bot Alert**\n\nIf you are reading this message, your Discord channel webhook is configured correctly! Bot is ready to send updates."
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=5)
        if r.status_code in [200, 204]:
            logging.info("Discord notification sent successfully! Check your Discord channel.")
            return True
        else:
            logging.error(f"Failed to send Discord message (HTTP status {r.status_code}): {r.text}")
            return False
    except Exception as e:
        logging.error(f"Discord connection exception occurred: {e}")
        return False

def main():
    print("====================================================")
    print("          TRADING BOT NOTIFICATION TESTER           ")
    print("====================================================\n")
    
    config = load_config()
    if not config:
        sys.exit(1)
        
    creds = config.get('credentials', {})
    tg_token = creds.get('telegram_token')
    tg_chat_id = creds.get('telegram_chat_id')
    discord_webhook = creds.get('discord_webhook')
    
    tg_success = test_telegram(tg_token, tg_chat_id)
    print("")
    discord_success = test_discord(discord_webhook)
    
    print("\n====================== STATUS ======================")
    if tg_token or tg_chat_id:
        print(f"Telegram Bot Status : {'[OK] PASS' if tg_success else '[FAIL] FAILED'}")
    else:
        print("Telegram Bot Status : [SKIPPED] (Not configured)")
        
    if discord_webhook:
        print(f"Discord Webhook     : {'[OK] PASS' if discord_success else '[FAIL] FAILED'}")
    else:
        print("Discord Webhook     : [SKIPPED] (Not configured)")
    print("====================================================")

if __name__ == "__main__":
    main()
