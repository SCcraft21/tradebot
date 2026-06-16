import os
import time
import urllib.request
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class DhanClient:
    SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
    CACHE_PATH = os.path.join(os.path.dirname(__file__), "dhan_scrip_master.csv")

    def __init__(self, client_id: str = None, access_token: str = None):
        self.client_id = client_id
        self.access_token = access_token
        self.dhan = None
        self._df = None
        self._df_loaded_time = 0.0
        
        # Initialize dhanhq SDK if credentials are valid
        if self._has_valid_credentials():
            try:
                from dhanhq import dhanhq
                self.dhan = dhanhq(self.client_id, self.access_token)
                logger.info("Dhan API: Initialized dhanhq SDK client.")
            except Exception as e:
                logger.error(f"Dhan API: Failed to initialize dhanhq SDK: {e}")
        else:
            logger.warning("Dhan API: Credentials missing or placeholders. Operating in Mock/Paper mode.")

    def _has_valid_credentials(self) -> bool:
        if not self.client_id or not self.access_token:
            return False
        c_id = self.client_id.strip()
        tkn = self.access_token.strip()
        if not c_id or not tkn:
            return False
        if "YOUR_" in c_id or "placeholder" in c_id.lower() or c_id == "":
            return False
        return True

    def _download_scrip_master(self):
        logger.info(f"Dhan API: Downloading scrip master from {self.SCRIP_MASTER_URL}...")
        try:
            req = urllib.request.Request(
                self.SCRIP_MASTER_URL,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                with open(self.CACHE_PATH, 'wb') as f:
                    f.write(response.read())
            logger.info("Dhan API: Scrip master downloaded and cached successfully.")
        except Exception as e:
            logger.error(f"Dhan API: Failed to download scrip master: {e}")
            raise

    def load_scrip_master(self) -> pd.DataFrame:
        cache_valid = False
        if os.path.exists(self.CACHE_PATH):
            mtime = os.path.getmtime(self.CACHE_PATH)
            # Re-download if cache is older than 24 hours
            if time.time() - mtime < 24 * 3600:
                cache_valid = True
                
        if not cache_valid:
            try:
                self._download_scrip_master()
            except Exception:
                if not os.path.exists(self.CACHE_PATH):
                    raise RuntimeError("No cached scrip master available and download failed.")
                logger.warning("Dhan API: Using stale cached scrip master because download failed.")
                
        # Load the CSV
        if self._df is None or time.time() - self._df_loaded_time > 3600:
            logger.info("Dhan API: Loading scrip master into memory...")
            try:
                self._df = pd.read_csv(self.CACHE_PATH, low_memory=False)
                self._df_loaded_time = time.time()
                logger.info(f"Dhan API: Loaded {len(self._df)} instruments from scrip master.")
            except Exception as e:
                logger.error(f"Dhan API: Failed to load cached scrip master: {e}")
                if self._df is None:
                    raise
        return self._df

    def find_instrument_id(self, symbol: str, segment: str, strike: float = None, option_type: str = None, expiry_date: str = None) -> int:
        """
        Finds the security ID of an instrument in the Dhan scrip master.
        symbol: e.g. 'TCS' or 'NIFTY' (without .NS suffix)
        segment: 'E' (Equity), 'I' (Index), 'D' (Derivatives/FNO)
        strike: Option strike price (float)
        option_type: 'CE' or 'PE'
        expiry_date: Date string in 'YYYY-MM-DD' format
        """
        df = self.load_scrip_master()
        
        symbol = symbol.upper()
        if symbol.endswith('.NS'):
            symbol = symbol[:-3]
            
        segment_df = df[df['SEM_SEGMENT'] == segment]
        
        if segment == 'E':
            matches = segment_df[
                (segment_df['SEM_EXM_EXCH_ID'] == 'NSE') & 
                (segment_df['SEM_TRADING_SYMBOL'] == symbol)
            ]
        elif segment == 'I':
            matches = segment_df[
                (segment_df['SEM_EXM_EXCH_ID'] == 'NSE') & 
                ((segment_df['SEM_TRADING_SYMBOL'] == symbol) | 
                 (segment_df['SEM_CUSTOM_SYMBOL'] == symbol) | 
                 (segment_df['SEM_CUSTOM_SYMBOL'].str.lower() == f"{symbol.lower()} 50"))
            ]
        elif segment == 'D':
            matches = segment_df[
                (segment_df['SEM_EXM_EXCH_ID'] == 'NSE') & 
                (segment_df['SEM_OPTION_TYPE'] == option_type) &
                (segment_df['SEM_STRIKE_PRICE'] == strike)
            ]
            
            def matches_symbol(val):
                if not isinstance(val, str):
                    return False
                return val.split('-')[0].upper() == symbol
                
            matches = matches[matches['SEM_TRADING_SYMBOL'].apply(matches_symbol)]
            
            if expiry_date:
                matches = matches[matches['SEM_EXPIRY_DATE'].str.startswith(expiry_date, na=False)]
        else:
            return None
            
        if matches.empty:
            logger.warning(f"Dhan API: Instrument not found for Symbol: {symbol}, Segment: {segment}, Strike: {strike}, Expiry: {expiry_date}")
            return None
            
        security_id = int(matches.iloc[0]['SEM_SMST_SECURITY_ID'])
        return security_id

    def place_order_fno(self, security_id: int, buy_or_sell: str, quantity: int, price: float = None) -> dict:
        """
        Places an F&O order on Dhan.
        security_id: Dhan instrument security ID (int)
        buy_or_sell: 'BUY' or 'SELL'
        quantity: number of units/contracts (multiple of lot size)
        price: limit price. If None or 0, places a market order.
        """
        if not self.dhan:
            logger.warning(f"[MOCK DHAN] Placing F&O order | Security ID: {security_id} | {buy_or_sell} | Qty: {quantity} | Price: {price}")
            return {"status": "success", "data": {"orderId": "MOCK_ORDER_12345"}}
            
        try:
            tx_type = self.dhan.BUY if buy_or_sell.upper() == 'BUY' else self.dhan.SELL
            order_type = self.dhan.LIMIT if price and price > 0 else self.dhan.MARKET
            limit_price = float(price) if price and price > 0 else 0.0
            
            res = self.dhan.place_order(
                security_id=str(security_id),
                exchange_segment=self.dhan.NSE_FNO,
                transaction_type=tx_type,
                quantity=int(quantity),
                order_type=order_type,
                product_type=self.dhan.MARGIN,  # MARGIN is standard for F&O carry forward
                price=limit_price,
                validity="DAY"
            )
            logger.info(f"Dhan API: Placed F&O order. Response: {res}")
            return res
        except Exception as e:
            logger.error(f"Dhan API: Error placing F&O order: {e}")
            raise

    def get_live_option_chain_data(self, underlying_symbol: str, expiry_date: str) -> dict:
        """
        Fetches option chain from Dhan for a given underlying symbol and expiry date.
        underlying_symbol: e.g. 'TCS' or 'NIFTY'
        expiry_date: 'YYYY-MM-DD'
        """
        if not self.dhan:
            return None
            
        try:
            underlying_segment = 'IDX_I' if underlying_symbol.upper() in ('NIFTY', 'BANKNIFTY', 'FINNIFTY') else 'NSE_EQ'
            seg_code = 'I' if underlying_segment == 'IDX_I' else 'E'
            under_security_id = self.find_instrument_id(underlying_symbol, seg_code)
            
            if not under_security_id:
                logger.warning(f"Dhan API: Underlying security ID not found for {underlying_symbol}")
                return None
                
            res = self.dhan.option_chain(
                under_security_id=int(under_security_id),
                under_exchange_segment=underlying_segment,
                expiry=expiry_date
            )
            return res
        except Exception as e:
            logger.error(f"Dhan API: Error fetching option chain: {e}")
            return None

    def get_expiry_dates(self, symbol: str) -> list:
        """
        Get sorted list of unique expiry dates for a given symbol from scrip master.
        Returns: list of strings formatted as 'YYYY-MM-DD'
        """
        import datetime
        try:
            df = self.load_scrip_master()
            symbol = symbol.upper()
            if symbol.endswith('.NS'):
                symbol = symbol[:-3]
                
            segment_df = df[
                (df['SEM_SEGMENT'] == 'D') & 
                (df['SEM_EXM_EXCH_ID'] == 'NSE')
            ]
            
            def matches_symbol(val):
                if not isinstance(val, str):
                    return False
                return val.split('-')[0].upper() == symbol
                
            matches = segment_df[segment_df['SEM_TRADING_SYMBOL'].apply(matches_symbol)]
            if matches.empty:
                return []
                
            expiries = matches['SEM_EXPIRY_DATE'].dropna().unique()
            parsed_dates = []
            for exp in expiries:
                try:
                    date_str = str(exp).split(' ')[0]
                    # Verify it's a valid date string YYYY-MM-DD
                    datetime.datetime.strptime(date_str, "%Y-%m-%d")
                    parsed_dates.append(date_str)
                except Exception:
                    continue
            return sorted(list(set(parsed_dates)))
        except Exception as e:
            logger.error(f"Dhan API: Error getting expiry dates for {symbol}: {e}")
            return []

