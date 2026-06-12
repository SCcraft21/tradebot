import ccxt
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class Execution:
    def __init__(self, exchange: ccxt.Exchange, paper: bool):
        self.exchange = exchange
        self.paper = paper

    def execute_market_buy(self, symbol: str, amount: float) -> Optional[Dict]:
        if self.paper:
            logger.info(f"[PAPER] Executed MARKET BUY for {amount} {symbol}")
            return {'status': 'closed', 'symbol': symbol, 'amount': amount, 'side': 'buy', 'type': 'market', 'price': None}

        try:
            logger.info(f"[LIVE] Executing MARKET BUY for {amount} {symbol}")
            order = self.exchange.create_market_buy_order(symbol, amount)
            logger.info(f"Order executed: {order['id']}")
            return order
        except Exception as e:
            logger.error(f"Failed to execute buy order for {symbol}: {e}")
            return None

    def execute_limit_sell(self, symbol: str, amount: float, price: float) -> Optional[Dict]:
        if self.paper:
            logger.info(f"[PAPER] Placed LIMIT SELL (Take Profit) for {amount} {symbol} at {price:.2f}")
            return {'status': 'open', 'symbol': symbol, 'amount': amount, 'side': 'sell', 'type': 'limit', 'price': price}

        try:
            logger.info(f"[LIVE] Placing LIMIT SELL for {amount} {symbol} at {price:.2f}")
            order = self.exchange.create_limit_sell_order(symbol, amount, price)
            return order
        except Exception as e:
            logger.error(f"Failed to execute limit sell (TP) for {symbol}: {e}")
            return None
            
    def execute_stop_market_sell(self, symbol: str, amount: float, stop_price: float) -> Optional[Dict]:
        if self.paper:
            logger.info(f"[PAPER] Placed STOP MARKET SELL (Stop Loss) for {amount} {symbol} at {stop_price:.2f}")
            return {'status': 'open', 'symbol': symbol, 'amount': amount, 'side': 'sell', 'type': 'stop', 'stopPrice': stop_price}

        try:
            logger.info(f"[LIVE] Placing STOP MARKET SELL for {amount} {symbol} at {stop_price:.2f}")
            params = {'stopPrice': stop_price}
            order = self.exchange.create_order(symbol, 'market', 'sell', amount, None, params)
            return order
        except Exception as e:
            logger.error(f"Failed to execute stop sell (SL) for {symbol}: {e}")
            return None
