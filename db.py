import os
import json
import logging
import urllib.parse
from peewee import SqliteDatabase, PostgresqlDatabase, Model, CharField, FloatField, IntegerField, DateTimeField, TextField
import datetime

logger = logging.getLogger(__name__)

# If DATABASE_URL is set in environment (standard on cloud hosts), use Postgres. Otherwise fallback to SQLite.
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip() or None

def parse_db_url(db_url):
    db_name = None
    db_user = None
    db_password = None
    db_host = None
    db_port = 5432
    
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    if not db_url.startswith("postgresql://"):
        raise ValueError("Invalid protocol, must be postgresql:// or postgres://")
        
    url_body = db_url[len("postgresql://"):]
    
    if '@' in url_body:
        creds, host_part = url_body.rsplit('@', 1)
        if ':' in creds:
            db_user, db_password = creds.split(':', 1)
        else:
            db_user = creds
            db_password = None
    else:
        host_part = url_body
        db_user = None
        db_password = None
        
    if '/' in host_part:
        host_port, path_query = host_part.split('/', 1)
        if '?' in path_query:
            db_name, _ = path_query.split('?', 1)
        else:
            db_name = path_query
    else:
        host_port = host_part
        db_name = None
        
    if ':' in host_port:
        db_host, port_str = host_port.rsplit(':', 1)
        try:
            db_port = int(port_str)
        except ValueError:
            db_host = host_port
            db_port = 5432
    else:
        db_host = host_port
        
    if db_name:
        db_name = urllib.parse.unquote(db_name)
    if db_user:
        db_user = urllib.parse.unquote(db_user)
    if db_password:
        db_password = urllib.parse.unquote(db_password)
        
    return db_name, db_user, db_password, db_host, db_port

if DATABASE_URL and (DATABASE_URL.startswith('postgres://') or DATABASE_URL.startswith('postgresql://')):
    try:
        db_name, db_user, db_password, db_host, db_port = parse_db_url(DATABASE_URL)
        db = PostgresqlDatabase(
            db_name,
            user=db_user,
            password=db_password,
            host=db_host,
            port=db_port,
            sslmode='require' # Neon/Supabase require SSL
        )
        logger.info("Database: Initialized PostgreSQL database connection from DATABASE_URL.")
    except Exception as e:
        logger.error(f"Failed to initialize PostgreSQL connection: {e}. Falling back to SQLite.")
        db = SqliteDatabase('trading_state.db')
else:
    DB_PATH = 'trading_state.db'
    db = SqliteDatabase(DB_PATH)

class BaseModel(Model):
    class Meta:
        database = db

class CryptoTradeRecord(BaseModel):
    symbol = CharField(unique=True)
    amount = FloatField()
    entry_price = FloatField()
    take_profit = FloatField()
    stop_loss = FloatField()
    direction = CharField(default='BUY')
    created_at = DateTimeField(default=datetime.datetime.now)

class StockSpreadRecord(BaseModel):
    symbol = CharField(unique=True)
    strategy_type = CharField()
    expiration = CharField()
    dte = IntegerField()
    underlying_price = FloatField()
    short_strike = FloatField()
    long_strike = FloatField()
    net_credit = FloatField()
    spread_width = FloatField()
    contracts = IntegerField()
    margin_lockup = FloatField()
    raw_spread_info = TextField()  # Stored as JSON string for other properties
    created_at = DateTimeField(default=datetime.datetime.now)

class TradeHistoryRecord(BaseModel):
    asset_type = CharField() # 'crypto' or 'stock_options'
    symbol = CharField()
    direction = CharField() # 'BUY' (crypto) or strategy_type (options)
    amount = FloatField() # amount (crypto) or contracts count (options)
    entry_price = FloatField()
    exit_price = FloatField()
    pnl = FloatField()
    reason = CharField() # e.g. 'TP', 'SL', 'VETO', 'CLOSEALL', 'EXPIRATION'
    closed_at = DateTimeField(default=datetime.datetime.now)

def init_db():
    if db.is_closed():
        db.connect()
    db.create_tables([CryptoTradeRecord, StockSpreadRecord, TradeHistoryRecord], safe=True)
    
    # Run migration to add direction column if missing
    try:
        db.execute_sql("ALTER TABLE cryptotraderecord ADD COLUMN direction VARCHAR(255) DEFAULT 'BUY'")
        logger.info("Database Migration: Successfully added 'direction' column to CryptoTradeRecord table.")
    except Exception:
        # Ignore if the column already exists
        pass
        
    logger.info(f"Database trading_state.db initialized and tables verified.")

def save_crypto_trade(symbol: str, amount: float, entry_price: float, tp: float, sl: float, direction: str = 'BUY'):
    try:
        # Delete if exists to avoid unique constraint issue
        CryptoTradeRecord.delete().where(CryptoTradeRecord.symbol == symbol).execute()
        CryptoTradeRecord.create(
            symbol=symbol,
            amount=amount,
            entry_price=entry_price,
            take_profit=tp,
            stop_loss=sl,
            direction=direction
        )
        logger.info(f"DB: Saved active trade for {symbol}")
    except Exception as e:
        logger.error(f"DB Error saving crypto trade: {e}")

def remove_crypto_trade(symbol: str):
    try:
        query = CryptoTradeRecord.delete().where(CryptoTradeRecord.symbol == symbol)
        query.execute()
        logger.info(f"DB: Removed active trade for {symbol}")
    except Exception as e:
        logger.error(f"DB Error removing crypto trade: {e}")

def load_all_crypto_trades() -> dict:
    try:
        trades = {}
        for r in CryptoTradeRecord.select():
            trades[r.symbol] = {
                'amount': r.amount,
                'entry_price': r.entry_price,
                'take_profit': r.take_profit,
                'stop_loss': r.stop_loss,
                'direction': getattr(r, 'direction', 'BUY')
            }
        return trades
    except Exception as e:
        logger.error(f"DB Error loading crypto trades: {e}")
        return {}

def save_stock_spread(symbol: str, spread_info: dict, contracts: int, margin_lockup: float = None):
    try:
        # Delete if exists to avoid unique constraint issue
        StockSpreadRecord.delete().where(StockSpreadRecord.symbol == symbol).execute()
        width = spread_info['spread_width']
        if margin_lockup is None:
            margin_lockup = spread_info.get('margin_lockup', width * 100.0 * contracts)
            
        spread_copy = spread_info.copy()
        spread_copy['contracts'] = contracts
        spread_copy['margin_lockup'] = margin_lockup
        
        StockSpreadRecord.create(
            symbol=symbol,
            strategy_type=spread_info['strategy_type'],
            expiration=spread_info['expiration'],
            dte=spread_info['dte'],
            underlying_price=spread_info['underlying_price'],
            short_strike=spread_info.get('short_strike', 0.0),
            long_strike=spread_info.get('long_strike', 0.0),
            net_credit=spread_info['net_credit'],
            spread_width=width,
            contracts=contracts,
            margin_lockup=margin_lockup,
            raw_spread_info=json.dumps(spread_copy)
        )
        logger.info(f"DB: Saved active options spread for {symbol}")
    except Exception as e:
        logger.error(f"DB Error saving stock spread: {e}")

def remove_stock_spread(symbol: str):
    try:
        query = StockSpreadRecord.delete().where(StockSpreadRecord.symbol == symbol)
        query.execute()
        logger.info(f"DB: Removed active options spread for {symbol}")
    except Exception as e:
        logger.error(f"DB Error removing stock spread: {e}")

def load_all_stock_spreads() -> dict:
    try:
        spreads = {}
        for r in StockSpreadRecord.select():
            spread_info = json.loads(r.raw_spread_info)
            spread_info['contracts'] = r.contracts
            spread_info['margin_lockup'] = r.margin_lockup
            spreads[r.symbol] = spread_info
        return spreads
    except Exception as e:
        logger.error(f"DB Error loading stock spreads: {e}")
        return {}

def save_closed_trade(asset_type: str, symbol: str, direction: str, amount: float, entry_price: float, exit_price: float, pnl: float, reason: str):
    try:
        TradeHistoryRecord.create(
            asset_type=asset_type,
            symbol=symbol,
            direction=direction,
            amount=amount,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            reason=reason
        )
        logger.info(f"DB: Saved closed trade history for {symbol} | PnL: {pnl:.2f}")
    except Exception as e:
        logger.error(f"DB Error saving closed trade history: {e}")

def load_closed_trades_history(limit: int = 20) -> list:
    try:
        records = []
        for r in TradeHistoryRecord.select().order_by(TradeHistoryRecord.closed_at.desc()).limit(limit):
            records.append({
                'asset_type': r.asset_type,
                'symbol': r.symbol,
                'direction': r.direction,
                'amount': r.amount,
                'entry_price': r.entry_price,
                'exit_price': r.exit_price,
                'pnl': r.pnl,
                'reason': r.reason,
                'closed_at': r.closed_at
            })
        return records
    except Exception as e:
        logger.error(f"DB Error loading closed trades history: {e}")
        return []
