import requests
import json
import logging
import pandas as pd
import re

logger = logging.getLogger(__name__)

def _clean_json_response(text: str) -> str:
    """
    Cleans up any conversational prefix/suffix or markdown fences (e.g. ```json ... ```) 
    from the LLM text output to isolate the JSON object.
    """
    text = text.strip()
    if "```" in text:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]
    return text

def _is_safe_symbol(symbol: str) -> bool:
    """
    Validates that symbol is a standard ticker pattern containing only safe characters
    to prevent prompt injection exploits.
    """
    if not isinstance(symbol, str) or not symbol:
        return False
    return bool(re.match(r'^[A-Za-z0-9/^._-]+$', symbol))

class TradingBrain:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash", veto_power: bool = True):
        self.api_key = api_key
        self.model = model
        self.veto_power = veto_power
        
    def _is_api_key_valid(self) -> bool:
        if not self.api_key:
            return False
        # Treat placeholders as invalid
        val = self.api_key.strip()
        if val.startswith("YOUR_") or val == "" or "placeholder" in val.lower():
            return False
        return True

    def analyze_market(self, symbol: str, timeframe: str, df: pd.DataFrame) -> dict:
        """
        Analyzes recent market data and indicators using Gemini API.
        Returns a dict:
        {
            "decision": "APPROVE" | "VETO",
            "confidence": float (0.0 to 1.0),
            "rationale": str
        }
        """
        # Fallback response
        fallback = {
            "decision": "APPROVE",
            "confidence": 1.0,
            "rationale": "Brain API key not configured or request failed; automatically approving trade."
        }
        
        if not _is_safe_symbol(symbol):
            logger.warning(f"Unsafe symbol detected: '{symbol}'. Bypassing brain check.")
            return fallback
            
        if not self._is_api_key_valid():
            logger.info("Gemini API key is invalid or placeholder. Bypassing brain check (defaulting to APPROVE).")
            return fallback

        if df.empty or len(df) < 5:
            logger.warning(f"DataFrame is empty or too small for analysis for symbol {symbol}.")
            return fallback

        # Prepare recent candle data to present to the brain (last 15 candles)
        recent_df = df.tail(15).copy()
        
        data_summary = []
        for idx, row in recent_df.iterrows():
            candle_str = (
                f"Time: {idx} | "
                f"O: {row['open']:.2f}, H: {row['high']:.2f}, L: {row['low']:.2f}, C: {row['close']:.2f}, V: {int(row['volume'])} | "
                f"RSI: {row.get('rsi', 0.0):.1f} | "
                f"VWAP: {row.get('vwap', 0.0):.2f} | "
                f"EMA50: {row.get('ema50', 0.0):.2f} | "
                f"EMA200: {row.get('ema200', 0.0):.2f} | "
                f"MACD: {row.get('macd', 0.0):.2f}/{row.get('macd_signal', 0.0):.2f}"
            )
            data_summary.append(candle_str)
            
        data_text = "\n".join(data_summary)
        
        # Build prompt
        prompt = (
            f"You are a cognitive expert swing-trading agent ('TradingBrain') for the symbol {symbol} on {timeframe} timeframe.\n"
            f"A technical swing-trading strategy has just triggered a BUY signal on the most recent completed candle.\n\n"
            f"Here is the historical context of the last 15 candles:\n"
            f"{data_text}\n\n"
            f"Please review the technical indicators (specifically RSI for momentum strength and VWAP showing market value strength) and raw price action context to determine if this BUY signal is high quality or a trap (e.g. consolidation breakout vs. local top exhaustion, volume confirmation vs. churn, rsi oversold recovery vs. extreme panic sell off).\n\n"
            f"Return your decision strictly in JSON format matching the schema below. Do NOT write any code blocks, backticks, or extra markdown text. Output a single, clean JSON object:\n"
            f"{{\n"
            f"  \"decision\": \"APPROVE\" or \"VETO\",\n"
            f"  \"confidence\": <float value between 0.0 and 1.0>,\n"
            f"  \"rationale\": \"<a brief 1-2 sentence explanation of your decision>\"\n"
            f"}}\n"
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        try:
            # Short timeout to avoid blocking execution loops
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                res_data = response.json()
                text_response = res_data['candidates'][0]['content']['parts'][0]['text']
                # Parse JSON response
                cleaned_response = _clean_json_response(text_response)
                parsed = json.loads(cleaned_response)
                
                # Normalize decision
                decision = parsed.get("decision", "APPROVE").strip().upper()
                if decision not in ["APPROVE", "VETO"]:
                    decision = "APPROVE"
                    
                confidence = float(parsed.get("confidence", 1.0))
                rationale = parsed.get("rationale", "No rationale provided by brain.").strip()
                
                logger.info(f"Brain decision for {symbol}: {decision} (Confidence: {confidence:.2f}) | Rationale: {rationale}")
                return {
                    "decision": decision,
                    "confidence": confidence,
                    "rationale": rationale
                }
            else:
                logger.error(f"Gemini API returned error status {response.status_code}: {response.text}")
                return fallback
        except Exception as e:
            logger.error(f"Error querying Gemini API: {e}")
            return fallback

    def explain_trade(self, symbol: str, action: str, entry: float, tp: float, sl: float, rationale: str) -> str:
        """
        Generates a natural language explanation/commentary for a trade.
        """
        if not self._is_api_key_valid():
            return f"Action: {action} on {symbol} at {entry:.2f}. Strategy criteria met."
            
        prompt = (
            f"Write a short, engaging, professional trading update (under 60 words) describing the trade details:\n"
            f"- Symbol: {symbol}\n"
            f"- Action: {action}\n"
            f"- Entry Price: {entry:.2f}\n"
            f"- Take Profit: {tp:.2f}\n"
            f"- Stop Loss: {sl:.2f}\n"
            f"- Technical Rationale: {rationale}\n\n"
            f"Make it read like a quick update from a quant desk. Keep it concise."
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                res_data = response.json()
                text_response = res_data['candidates'][0]['content']['parts'][0]['text']
                return text_response.strip()
        except Exception as e:
            logger.error(f"Error generating trade explanation: {e}")
            
        return f"Quant Desk: Executed {action} on {symbol} at {entry:.2f} (TP: {tp:.2f}/SL: {sl:.2f}). Rationale: {rationale}."

    def analyze_options_spread(self, symbol: str, spread_info: dict, df: pd.DataFrame) -> dict:
        """
        Analyzes stock price action and proposed option spread parameters using Gemini API.
        Returns a dict:
        {
            "decision": "APPROVE" | "VETO",
            "confidence": float (0.0 to 1.0),
            "rationale": str
        }
        """
        # Fallback response
        fallback = {
            "decision": "APPROVE",
            "confidence": 1.0,
            "rationale": "Brain API key not configured or request failed; automatically approving trade."
        }
        
        if not _is_safe_symbol(symbol):
            logger.warning(f"Unsafe symbol detected: '{symbol}'. Bypassing brain check.")
            return fallback
            
        if not self._is_api_key_valid():
            logger.info("Gemini API key is invalid or placeholder. Bypassing brain check for stock options (defaulting to APPROVE).")
            return fallback

        if df.empty or len(df) < 5:
            logger.warning(f"DataFrame is empty or too small for analysis for symbol {symbol}.")
            return fallback

        # Ensure indicators exist on options df
        if 'rsi' not in df.columns:
            from ta.momentum import RSIIndicator
            from ta.trend import EMAIndicator
            df = df.copy()
            df['rsi'] = RSIIndicator(close=df['Close'], window=14).rsi()
            df['ema200'] = EMAIndicator(close=df['Close'], window=200).ema_indicator()
            tp = (df['High'] + df['Low'] + df['Close']) / 3.0
            tp_vol = tp * df['Volume']
            cum_tp_vol = tp_vol.rolling(window=14).sum()
            cum_vol = df['Volume'].rolling(window=14).sum()
            df['vwap'] = (cum_tp_vol / cum_vol).fillna(df['Close'])
            
        # Prepare recent daily candles to present to the brain (last 15 rows)
        recent_df = df.tail(15).copy()
        
        data_summary = []
        for idx, row in recent_df.iterrows():
            date_str = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)
            candle_str = (
                f"Date: {date_str} | "
                f"O: {row['Open']:.2f}, H: {row['High']:.2f}, L: {row['Low']:.2f}, C: {row['Close']:.2f}, V: {int(row['Volume'])} | "
                f"RSI: {row.get('rsi', 0.0):.1f} | VWAP: {row.get('vwap', 0.0):.2f} | EMA200: {row.get('ema200', 0.0):.2f}"
            )
            data_summary.append(candle_str)
            
        data_text = "\n".join(data_summary)
        
        # Format spread details based on strategy type
        strat_type = spread_info.get("strategy_type", "BULL_PUT")
        
        spread_details = f"Strategy: {strat_type}\n"
        if strat_type in ('MOMENTUM_CALL_BUY', 'ORB_CALL_BUY'):
            spread_details += f"- Buy Long Call Strike: {spread_info.get('long_strike'):.2f}\n"
        elif strat_type in ('MOMENTUM_PUT_BUY', 'ORB_PUT_BUY'):
            spread_details += f"- Buy Long Put Strike: {spread_info.get('long_strike'):.2f}\n"
        elif strat_type == 'CASH_SECURED_PUT':
            spread_details += f"- Sell Short Put Strike: {spread_info.get('short_strike'):.2f}\n"
        elif strat_type == 'COVERED_CALL':
            spread_details += f"- Buy Underlying Stock + Sell Short Call Strike: {spread_info.get('short_strike'):.2f}\n"
        elif strat_type == 'CALENDAR_SPREAD':
            spread_details += f"- Sell near-term strike & Buy longer-term strike: {spread_info.get('short_strike'):.2f}\n"
        elif strat_type == 'IRON_BUTTERFLY':
            spread_details += (
                f"- Sell Put/Call Strike: {spread_info.get('short_put_strike'):.2f}\n"
                f"- Buy Put Strike: {spread_info.get('long_put_strike'):.2f}\n"
                f"- Buy Call Strike: {spread_info.get('long_call_strike'):.2f}\n"
            )
        elif strat_type == 'BULL_PUT':
            spread_details += (
                f"- Sell Short Put Strike: {spread_info.get('short_strike'):.2f}\n"
                f"- Buy Long Put Strike: {spread_info.get('long_strike'):.2f}\n"
            )
        elif strat_type == 'BEAR_CALL':
            spread_details += (
                f"- Sell Short Call Strike: {spread_info.get('short_strike'):.2f}\n"
                f"- Buy Long Call Strike: {spread_info.get('long_strike'):.2f}\n"
            )
        else: # IRON_CONDOR
            spread_details += (
                f"- Put Wing: Sell {spread_info.get('short_put_strike'):.2f} / Buy {spread_info.get('long_put_strike'):.2f}\n"
                f"- Call Wing: Sell {spread_info.get('short_call_strike'):.2f} / Buy {spread_info.get('long_call_strike'):.2f}\n"
            )
            
        spread_summary = (
            f"Symbol: {symbol}\n"
            f"Underlying Current Price: {spread_info.get('underlying_price'):.2f}\n"
            f"{spread_details}"
            f"Expiration Days to Expiration (DTE): {spread_info.get('dte')}\n"
            f"Estimated Net Credit collected per contract: ₹{spread_info.get('net_credit'):.2f} (Spread Width: ₹{spread_info.get('spread_width'):.2f})\n"
            f"Max Profit: ₹{spread_info.get('max_profit'):.2f} | Max Loss: ₹{spread_info.get('max_loss'):.2f}\n"
            f"Estimated IV Rank: {spread_info.get('iv_rank'):.2f}%"
        )
        
        # Build prompt
        prompt = (
            f"You are a cognitive expert options strategist ('TradingBrain') for the symbol {symbol}.\n"
            f"A multi-regime options trading strategy has triggered a trade entry candidate based on the current regime.\n\n"
            f"Here are the options trade details:\n"
            f"{spread_summary}\n\n"
            f"Here is the daily historical stock price action context of the last 15 days:\n"
            f"{data_text}\n\n"
            f"Please review the stock price trend, consolidation levels, potential support/resistance, indicators (RSI, VWAP, EMA), and return on risk to determine if this options trade is high quality or a high-risk trap.\n"
            f"Guidelines for options strategies:\n"
            f"- Momentum Breakout Buy (MOMENTUM_CALL_BUY / MOMENTUM_PUT_BUY): Look for clear trend breakouts, high volume, and RSI showing strong momentum (RSI > 60 for call, < 40 for put) with price diverging from VWAP.\n"
            f"- Opening Range Breakout (ORB_CALL_BUY / ORB_PUT_BUY): Look for sudden volume expansions at market open breaking past key daily ranges.\n"
            f"- Iron Condor / Iron Butterfly: Look for low-momentum, range-bound consolidation, flat moving averages, and high implied volatility (IV Rank > 30-50%). Veto if index/stock is trending hard.\n"
            f"- Covered Call / Cash-Secured Put: Look for strong/moderate bullish trends, stable/low volatility, or consolidation near key support levels.\n"
            f"- Calendar Spread: Look for range-bound or slightly bullish outlook in low implied volatility regimes (IV Rank < 15-20%) expecting a future vol expansion.\n\n"
            f"Return your decision strictly in JSON format matching the schema below. Do NOT write any code blocks, backticks, or extra markdown text. Output a single, clean JSON object:\n"
            f"{{\n"
            f"  \"decision\": \"APPROVE\" or \"VETO\",\n"
            f"  \"confidence\": <float value between 0.0 and 1.0>,\n"
            f"  \"rationale\": \"<a brief 1-2 sentence explanation of your decision>\"\n"
            f"}}\n"
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                res_data = response.json()
                text_response = res_data['candidates'][0]['content']['parts'][0]['text']
                cleaned_response = _clean_json_response(text_response)
                parsed = json.loads(cleaned_response)
                
                decision = parsed.get("decision", "APPROVE").strip().upper()
                if decision not in ["APPROVE", "VETO"]:
                    decision = "APPROVE"
                    
                confidence = float(parsed.get("confidence", 1.0))
                rationale = parsed.get("rationale", "No rationale provided by brain.").strip()
                
                logger.info(f"Brain options decision for {symbol}: {decision} (Confidence: {confidence:.2f}) | Rationale: {rationale}")
                return {
                    "decision": decision,
                    "confidence": confidence,
                    "rationale": rationale
                }
            else:
                logger.error(f"Gemini API returned error status {response.status_code}: {response.text}")
                return fallback
        except Exception as e:
            logger.error(f"Error querying Gemini API for options: {e}")
            return fallback

    def explain_options_spread(self, symbol: str, spread_info: dict, contracts: int, rationale: str) -> str:
        """
        Generates a natural language explanation/commentary for a stock options spread entry.
        """
        if not self._is_api_key_valid():
            strat = spread_info.get('strategy_type', 'BULL_PUT')
            if strat == 'BULL_PUT':
                return f"Entered options spread on {symbol} (BULL PUT): Sell ₹{spread_info['short_strike']:.2f} Put / Buy ₹{spread_info['long_strike']:.2f} Put | Net Credit: ₹{spread_info['net_credit']:.2f} | Contracts: {contracts}"
            elif strat == 'BEAR_CALL':
                return f"Entered options spread on {symbol} (BEAR CALL): Sell ₹{spread_info['short_strike']:.2f} Call / Buy ₹{spread_info['long_strike']:.2f} Call | Net Credit: ₹{spread_info['net_credit']:.2f} | Contracts: {contracts}"
            else:
                return f"Entered options spread on {symbol} (IRON CONDOR): Put ₹{spread_info['short_put_strike']:.2f}/₹{spread_info['long_put_strike']:.2f} | Call ₹{spread_info['short_call_strike']:.2f}/₹{spread_info['long_call_strike']:.2f} | Contracts: {contracts}"

        strat = spread_info.get('strategy_type', 'BULL_PUT')
        prompt = (
            f"Write a short, engaging, professional trading update (under 60 words) describing the options spread entry:\n"
            f"- Symbol: {symbol}\n"
            f"- Strategy: {strat}\n"
            f"- Strikes: Short Put {spread_info.get('short_put_strike')} / Long Put {spread_info.get('long_put_strike')} | Short Call {spread_info.get('short_call_strike')} / Long Call {spread_info.get('long_call_strike')} if Iron Condor, else Short Strike {spread_info.get('short_strike')} / Long Strike {spread_info.get('long_strike')}\n"
            f"- Net Credit: {spread_info.get('net_credit'):.2f}\n"
            f"- Contracts: {contracts}\n"
            f"- Underlying Price: {spread_info.get('underlying_price'):.2f}\n"
            f"- Technical Rationale: {rationale}\n\n"
            f"Make it read like a quick desk update. Keep it concise."
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {'Content-Type': 'application/json'}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                res_data = response.json()
                text_response = res_data['candidates'][0]['content']['parts'][0]['text']
                return text_response.strip()
        except Exception as e:
            logger.error(f"Error generating options trade explanation: {e}")
            
        # Fallback to simple description if LLM failed
        strat = spread_info.get('strategy_type', 'BULL_PUT')
        if strat == 'BULL_PUT':
            return f"Entered options spread on {symbol} (BULL PUT): Sell ₹{spread_info['short_strike']:.2f} Put / Buy ₹{spread_info['long_strike']:.2f} Put | Net Credit: ₹{spread_info['net_credit']:.2f} | Contracts: {contracts}"
        elif strat == 'BEAR_CALL':
            return f"Entered options spread on {symbol} (BEAR CALL): Sell ₹{spread_info['short_strike']:.2f} Call / Buy ₹{spread_info['long_strike']:.2f} Call | Net Credit: ₹{spread_info['net_credit']:.2f} | Contracts: {contracts}"
        else:
            return f"Entered options spread on {symbol} (IRON CONDOR): Put ₹{spread_info['short_put_strike']:.2f}/₹{spread_info['long_put_strike']:.2f} | Call ₹{spread_info['short_call_strike']:.2f}/₹{spread_info['long_call_strike']:.2f} | Contracts: {contracts}"
