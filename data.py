import ccxt
import pandas as pd
import time
import logging

logger = logging.getLogger(__name__)

class DataFetcher:
    def __init__(self, exchange_id: str, api_key: str, api_secret: str, paper: bool = True, demo_trading: bool = False, api_url: str = None):
        exchange_class = getattr(ccxt, exchange_id)
        
        is_placeholder = lambda val: (
            not val or 
            not isinstance(val, str) or 
            val.startswith("YOUR_") or 
            val.strip() == "" or 
            "placeholder" in val.lower()
        )
        
        config = {
            'enableRateLimit': True,
        }
        
        if api_url:
            config['urls'] = {
                'api': {
                    'public': api_url,
                    'private': api_url,
                    'futures': api_url,
                    'spot': api_url,
                    'v2': api_url,
                }
            }
            logger.info(f"Routing '{exchange_id}' API requests through custom URL: {api_url}")
        
        if not paper and not is_placeholder(api_key) and not is_placeholder(api_secret):
            config['apiKey'] = api_key
            config['secret'] = api_secret
        else:
            logger.info(f"Using anonymous/public access for exchange '{exchange_id}' as API keys are placeholder, missing, or paper trading is active.")
            
        self.exchange = exchange_class(config)
        
        if paper:
            # For paper trading, order execution is simulated locally, so we always fetch live mainnet data (highly reliable/unblocked)
            logger.info(f"Paper trading mode active. Fetching public market data from live mainnet '{exchange_id}' exchange.")
        else:
            # Only enable testnet/demo endpoints if live-trading on those specific sandboxes
            if demo_trading:
                self.exchange.enableDemoTrading(True)
                logger.info(f"Enabled Demo Trading mode for exchange '{exchange_id}'.")
            else:
                if is_placeholder(api_key):
                    self.exchange.set_sandbox_mode(True)
                    logger.info(f"Enabled Testnet/Sandbox mode for exchange '{exchange_id}'.")

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100, retries: int = 3) -> pd.DataFrame:
        """Fetch OHLCV historical data with pagination support for large limits."""
        import re
        if not symbol or not isinstance(symbol, str) or not re.match(r'^[A-Za-z0-9/^._-]+$', symbol):
            logger.warning(f"Rejected unsafe symbol input in fetch_ohlcv: '{symbol}'")
            return pd.DataFrame()
        if not timeframe or not isinstance(timeframe, str) or not re.match(r'^[a-zA-Z0-9]+$', timeframe):
            logger.warning(f"Rejected unsafe timeframe input in fetch_ohlcv: '{timeframe}'")
            return pd.DataFrame()
        # If limit is small, standard single request
        if limit <= 500:
            for attempt in range(retries):
                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)
                    return df
                except ccxt.NetworkError as e:
                    if attempt == retries - 1:
                        logger.error(f"Network error fetching {symbol}: {e}")
                    time.sleep(2 ** attempt)
                except ccxt.ExchangeError as e:
                    logger.error(f"Exchange error fetching {symbol}: {e}")
                    break
                except Exception as e:
                    logger.error(f"Unexpected error fetching {symbol}: {e}")
                    break
            return pd.DataFrame()
            
        # Paginated fetch for larger limits
        all_ohlcv = []
        try:
            # Timeframe in ms duration
            tf_sec = self.exchange.parse_timeframe(timeframe)
            tf_ms = tf_sec * 1000
            # Start "since" (limit * timeframe duration)
            since = self.exchange.milliseconds() - (limit * tf_ms)
        except Exception as e:
            logger.warning(f"Failed to calculate since timestamp: {e}. Defaulting to None.")
            since = None
            
        chunk_limit = 1000 if self.exchange.id != 'kraken' else 720
        
        last_since = None
        while len(all_ohlcv) < limit:
            current_limit = min(chunk_limit, limit - len(all_ohlcv))
            ohlcv = []
            fetched = False
            for attempt in range(retries):
                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=current_limit)
                    fetched = True
                    break
                except ccxt.NetworkError as e:
                    if attempt == retries - 1:
                        logger.error(f"Network error in paginated fetch for {symbol}: {e}")
                    time.sleep(2 ** attempt)
                except Exception as e:
                    logger.error(f"Error in paginated fetch for {symbol}: {e}")
                    break
            
            if not fetched or not ohlcv:
                break
                
            all_ohlcv.extend(ohlcv)
            next_since = ohlcv[-1][0] + 1
            
            # If since is not progressing, break to avoid infinite loop
            if last_since is not None and next_since <= last_since:
                break
            last_since = next_since
            since = next_since
            
            time.sleep(self.exchange.rateLimit / 1000.0)
            
        if not all_ohlcv:
            return pd.DataFrame()
            
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        # Drop duplicates
        df = df[~df.index.duplicated(keep='first')]
        df.sort_index(inplace=True)
        return df.tail(limit)
