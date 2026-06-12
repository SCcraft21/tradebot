import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Adjust path to find the packages
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from main import TradingBotEngine
from telegram_control import TelegramController

class TestTelegramControl(unittest.TestCase):
    def setUp(self):
        # Mock config
        self.config = {
            'trading': {
                'exchange': 'binance',
                'mode': 'paper',
                'symbols': ['BTC/USDT', 'ETH/USDT'],
                'timeframe': '15m'
            },
            'strategy': {
                'rsi_period': 14,
                'rsi_oversold': 35,
                'ema_period': 50,
                'ema_proximity_pct': 0.015
            },
            'risk': {
                'take_profit_pct': 0.05,
                'stop_loss_pct': 0.025,
                'max_capital_per_trade_pct': 0.15,
                'daily_loss_limit_pct': 0.10,
                'max_open_trades': 2
            },
            'credentials': {
                'api_key': 'mock_key',
                'api_secret': 'mock_secret',
                'telegram_token': 'mock_token',
                'telegram_chat_id': '12345'
            },
            'stocks': {
                'symbols': ['AAPL'],
                'target_dte': 40,
                'target_delta': -0.30,
                'spread_width': 5.0,
                'min_iv_rank:': 0.0,
                'ema_period': 200
            }
        }
        
        # Patch ccxt and yfinance to avoid network requests during setup
        with patch('main.DataFetcher'), patch('main.StockDataFetcher'):
            self.engine = TradingBotEngine(self.config, asset_mode='crypto')
            
        self.controller = self.engine.telegram

    @patch('telegram_control.requests.post')
    @patch('telegram_control.requests.get')
    def test_command_parsing(self, mock_get, mock_post):
        # 1. Mock status getUpdates response containing an unauthorized message and an authorized command
        mock_response_get = MagicMock()
        mock_response_get.status_code = 200
        mock_response_get.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "chat": {"id": 99999},  # Unauthorized chat id
                        "text": "/status"
                    }
                },
                {
                    "update_id": 101,
                    "message": {
                        "chat": {"id": 12345},  # Authorized chat id
                        "text": "/status"
                    }
                }
            ]
        }
        mock_get.return_value = mock_response_get
        
        # Poll commands
        self.controller.poll_commands()
        
        # Ensure it processed up to update_id 101
        self.assertEqual(self.controller.offset, 102)
        
        # Ensure it sent unauthorized warn message to 99999 AND status details to 12345
        self.assertEqual(mock_post.call_count, 2)
        
        # Verify call args
        first_call = mock_post.call_args_list[0]
        self.assertIn("Unauthorized!", first_call[1]['json']['text'])
        self.assertEqual(first_call[1]['json']['chat_id'], '99999')
        
        second_call = mock_post.call_args_list[1]
        self.assertIn("BOT STATUS REPORT", second_call[1]['json']['text'])
        self.assertEqual(second_call[1]['json']['chat_id'], '12345')

    @patch('telegram_control.requests.post')
    @patch('telegram_control.requests.get')
    def test_pause_resume_switch(self, mock_get, mock_post):
        # Pause command
        mock_response_get = MagicMock()
        mock_response_get.status_code = 200
        mock_response_get.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 200,
                    "message": {
                        "chat": {"id": 12345},
                        "text": "/pause"
                    }
                }
            ]
        }
        mock_get.return_value = mock_response_get
        
        # Poll
        self.controller.poll_commands()
        self.assertFalse(self.engine.trading_active)
        self.assertIn("PAUSED", mock_post.call_args[1]['json']['text'])
        
        # Resume command
        mock_response_get.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 201,
                    "message": {
                        "chat": {"id": 12345},
                        "text": "/resume"
                    }
                }
            ]
        }
        self.controller.poll_commands()
        self.assertTrue(self.engine.trading_active)
        self.assertIn("RESUMED", mock_post.call_args[1]['json']['text'])

        # Switch mode command (crypto -> stocks)
        self.assertEqual(self.engine.asset_mode, 'crypto')
        mock_response_get.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 202,
                    "message": {
                        "chat": {"id": 12345},
                        "text": "/switch"
                    }
                }
            ]
        }
        self.controller.poll_commands()
        self.assertEqual(self.engine.asset_mode, 'stocks')
        self.assertIn("switched successfully", mock_post.call_args[1]['json']['text'])

if __name__ == '__main__':
    unittest.main()
