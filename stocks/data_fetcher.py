import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import math
import logging

logger = logging.getLogger(__name__)

class StockDataFetcher:
    def __init__(self, risk_free_rate: float = 0.045):
        self.risk_free_rate = risk_free_rate

    def fetch_stock_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """Fetch stock historical price data using yfinance."""
        import re
        if not symbol or not isinstance(symbol, str) or not re.match(r'^[A-Za-z0-9/^._-]+$', symbol):
            logger.warning(f"Rejected unsafe symbol input in fetch_stock_history: '{symbol}'")
            return pd.DataFrame()
        if not period or not isinstance(period, str) or not re.match(r'^[a-zA-Z0-9]+$', period):
            logger.warning(f"Rejected unsafe period input in fetch_stock_history: '{period}'")
            return pd.DataFrame()
        if not interval or not isinstance(interval, str) or not re.match(r'^[a-zA-Z0-9]+$', interval):
            logger.warning(f"Rejected unsafe interval input in fetch_stock_history: '{interval}'")
            return pd.DataFrame()
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if not df.empty:
                df = df.dropna(subset=['Close'])
            if df.empty:
                logger.warning(f"No history found for stock {symbol}.")
            return df
        except Exception as e:
            logger.error(f"Error fetching history for {symbol}: {e}")
            return pd.DataFrame()

    def calculate_historical_volatility(self, df: pd.DataFrame, window: int = 20) -> float:
        """Calculate annualized historical volatility of the stock."""
        if len(df) < window:
            return 0.15  # Fallback 15% volatility
        
        # Daily returns
        log_returns = np.log(df['Close'] / df['Close'].shift(1))
        # 20-day rolling standard deviation, annualized (252 trading days)
        vol = log_returns.rolling(window=window).std() * math.sqrt(252)
        latest_vol = vol.iloc[-1]
        return latest_vol if not pd.isna(latest_vol) else 0.15

    def calculate_iv_rank(self, df: pd.DataFrame, current_iv: float) -> float:
        """
        Estimate IV Rank comparing current IV to the 1-year Historical Volatility range.
        Since yfinance does not provide historical options IV series, we use historical volatility
        as a proxy to determine if volatility is high or low.
        """
        if len(df) < 252:
            return 50.0  # Fallback to middle rank
        
        log_returns = np.log(df['Close'] / df['Close'].shift(1))
        rolling_vols = log_returns.rolling(window=20).std() * math.sqrt(252)
        rolling_vols = rolling_vols.dropna()
        
        if rolling_vols.empty:
            return 50.0
            
        min_vol = rolling_vols.min()
        max_vol = rolling_vols.max()
        
        if max_vol == min_vol:
            return 50.0
            
        iv_rank = ((current_iv - min_vol) / (max_vol - min_vol)) * 100.0
        return float(np.clip(iv_rank, 0.0, 100.0))

    def _norm_cdf(self, x: float) -> float:
        """Cumulative standard normal distribution function using math.erf."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def calculate_option_delta(self, S: float, K: float, T: float, sigma: float, option_type: str = 'put') -> float:
        """Calculate the option Delta using Black-Scholes formula."""
        if T <= 0 or sigma <= 0:
            return -0.5 if option_type == 'put' else 0.5
            
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        
        if option_type.lower() == 'put':
            return self._norm_cdf(d1) - 1.0
        else:
            return self._norm_cdf(d1)

    def fetch_option_chain(self, symbol: str, target_dte: int = 40) -> tuple:
        """
        Fetch options chain for the expiration closest to target_dte.
        Returns: (selected_expiration_str, puts_dataframe, calls_dataframe, current_stock_price)
        """
        S = None
        try:
            ticker = yf.Ticker(symbol)
            
            # Fetch current price (using latest valid closing price from history)
            history = ticker.history(period="5d")
            if not history.empty:
                history = history.dropna(subset=['Close'])
            if history.empty:
                logger.error(f"Cannot fetch stock price for {symbol}.")
                return None, pd.DataFrame(), pd.DataFrame(), 0.0
            S = history['Close'].iloc[-1]
            
            expirations = ticker.options
            if not expirations:
                logger.info(f"No option chains found for {symbol}. Falling back to synthetic option chain.")
                return self._generate_synthetic_option_chain(symbol, S, target_dte)
                
            # Find closest expiration to target DTE
            today = datetime.date.today()
            closest_expiration = None
            min_dte_diff = 9999
            selected_dte = 0
            
            for exp_str in expirations:
                exp_date = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if dte <= 5:  # Skip options expiring in less than 5 days
                    continue
                diff = abs(dte - target_dte)
                if diff < min_dte_diff:
                    min_dte_diff = diff
                    closest_expiration = exp_str
                    selected_dte = dte
            
            if not closest_expiration:
                logger.info(f"No valid option expiration close to {target_dte} DTE found for {symbol}. Falling back to synthetic option chain.")
                return self._generate_synthetic_option_chain(symbol, S, target_dte)
                
            opt_chain = ticker.option_chain(closest_expiration)
            puts = opt_chain.puts.copy()
            calls = opt_chain.calls.copy()
            
            # Enrich puts with DTE, Current Stock Price, and calculated Delta
            puts['dte'] = selected_dte
            puts['underlying_price'] = S
            
            T = selected_dte / 365.0
            
            p_deltas = []
            for _, row in puts.iterrows():
                sigma = row['impliedVolatility']
                if pd.isna(sigma) or sigma <= 0:
                    sigma = 0.25  # Fallback default IV
                delta = self.calculate_option_delta(S, row['strike'], T, sigma, 'put')
                p_deltas.append(delta)
            puts['delta'] = p_deltas
            puts = puts[(puts['bid'] > 0) & (puts['ask'] > 0)]
            puts['mid'] = (puts['bid'] + puts['ask']) / 2.0
            
            # Enrich calls with DTE, Current Stock Price, and calculated Delta
            calls['dte'] = selected_dte
            calls['underlying_price'] = S
            
            c_deltas = []
            for _, row in calls.iterrows():
                sigma = row['impliedVolatility']
                if pd.isna(sigma) or sigma <= 0:
                    sigma = 0.25  # Fallback default IV
                delta = self.calculate_option_delta(S, row['strike'], T, sigma, 'call')
                c_deltas.append(delta)
            calls['delta'] = c_deltas
            calls = calls[(calls['bid'] > 0) & (calls['ask'] > 0)]
            calls['mid'] = (calls['bid'] + calls['ask']) / 2.0
            
            logger.info(f"Fetched {len(puts)} puts and {len(calls)} calls for {symbol} expiring on {closest_expiration} ({selected_dte} DTE). Current price: ₹{S:.2f}")
            return closest_expiration, puts, calls, S
            
        except Exception as e:
            logger.error(f"Error fetching option chain for {symbol}: {e}")
            if S is None or S <= 0:
                logger.error(f"Cannot generate synthetic options chain for {symbol} because underlying price is unknown.")
                return None, pd.DataFrame(), pd.DataFrame(), 0.0
            
            logger.info("Falling back to synthetic option chain due to exception.")
            try:
                return self._generate_synthetic_option_chain(symbol, S, target_dte)
            except Exception as inner_e:
                logger.error(f"Failed to generate fallback synthetic options: {inner_e}")
                return None, pd.DataFrame(), pd.DataFrame(), S

    def _bs_put_price(self, S: float, K: float, T: float, sigma: float) -> float:
        """Calculate Black-Scholes Put Option Price."""
        if T <= 0:
            return max(0.0, K - S)
        if sigma <= 0:
            sigma = 0.01
            
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        put_price = K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(-d2) - S * self._norm_cdf(-d1)
        return max(0.0, put_price)

    def _bs_call_price(self, S: float, K: float, T: float, sigma: float) -> float:
        """Calculate Black-Scholes Call Option Price."""
        if T <= 0:
            return max(0.0, S - K)
        if sigma <= 0:
            sigma = 0.01
            
        d1 = (math.log(S / K) + (self.risk_free_rate + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        call_price = S * self._norm_cdf(d1) - K * math.exp(-self.risk_free_rate * T) * self._norm_cdf(d2)
        return max(0.0, call_price)

    def _generate_synthetic_option_chain(self, symbol: str, S: float, target_dte: int) -> tuple:
        """Generate a synthetic options chain for NSE/BSE stock using Black-Scholes formula."""
        logger.info(f"Generating synthetic options chain for {symbol} at underlying price: {S:.2f}")
        try:
            # Estimate volatility using historical stock price
            hist = self.fetch_stock_history(symbol, period="1y")
            sigma = self.calculate_historical_volatility(hist)
            if sigma <= 0 or pd.isna(sigma):
                sigma = 0.25  # Fallback to 25% volatility
                
            # Expiration date
            expiration_date = datetime.date.today() + datetime.timedelta(days=target_dte)
            expiration = expiration_date.strftime("%Y-%m-%d")
            
            # Step size based on stock price (e.g. 100 for Nifty, 20 for high priced, 5 for mid)
            if S > 15000:
                step = 100.0
            elif S > 5000:
                step = 50.0
            elif S > 1000:
                step = 20.0
            elif S > 500:
                step = 10.0
            elif S > 100:
                step = 5.0
            else:
                step = 1.0
                
            min_strike = math.floor((S * 0.85) / step) * step
            max_strike = math.ceil((S * 1.15) / step) * step
            strikes = np.arange(min_strike, max_strike + step, step)
            
            T = target_dte / 365.0
            
            puts_data = []
            calls_data = []
            for K in strikes:
                put_price = self._bs_put_price(S, K, T, sigma)
                put_delta = self.calculate_option_delta(S, K, T, sigma, 'put')
                put_bid = max(0.05, round(put_price * 0.98, 2))
                put_ask = max(0.10, round(put_price * 1.02, 2))
                
                puts_data.append({
                    'strike': K,
                    'bid': put_bid,
                    'ask': put_ask,
                    'mid': (put_bid + put_ask) / 2.0,
                    'impliedVolatility': sigma,
                    'dte': target_dte,
                    'underlying_price': S,
                    'delta': put_delta,
                    'contractSymbol': f"SYN-{symbol}-{expiration}-P-{int(K)}"
                })
                
                call_price = self._bs_call_price(S, K, T, sigma)
                call_delta = self.calculate_option_delta(S, K, T, sigma, 'call')
                call_bid = max(0.05, round(call_price * 0.98, 2))
                call_ask = max(0.10, round(call_price * 1.02, 2))
                
                calls_data.append({
                    'strike': K,
                    'bid': call_bid,
                    'ask': call_ask,
                    'mid': (call_bid + call_ask) / 2.0,
                    'impliedVolatility': sigma,
                    'dte': target_dte,
                    'underlying_price': S,
                    'delta': call_delta,
                    'contractSymbol': f"SYN-{symbol}-{expiration}-C-{int(K)}"
                })
                
            puts_df = pd.DataFrame(puts_data)
            calls_df = pd.DataFrame(calls_data)
            logger.info(f"Successfully generated synthetic option chains with {len(puts_df)} puts and {len(calls_df)} calls.")
            return expiration, puts_df, calls_df, S
        except Exception as e:
            logger.error(f"Failed to generate synthetic option chain for {symbol}: {e}")
            return None, pd.DataFrame(), pd.DataFrame(), S

    def fetch_all_nse_fo_symbols(self) -> list:
        """Fetch all dynamic stock symbols listed in the NSE F&O segment."""
        from stocks.options_risk import fetch_fo_lot_sizes
        try:
            dynamic_lots = fetch_fo_lot_sizes()
            symbols = []
            for sym in dynamic_lots.keys():
                s = sym.strip().upper()
                if not s or s in ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'):
                    continue
                if not s.endswith('.NS'):
                    symbols.append(f"{s}.NS")
                else:
                    symbols.append(s)
            if symbols:
                logger.info(f"Loaded {len(symbols)} NSE F&O stock symbols dynamically.")
                return sorted(symbols)
        except Exception as e:
            logger.error(f"Failed to fetch dynamic F&O symbols: {e}")
            
        return ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS"]
