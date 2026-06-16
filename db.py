import os
import json
import logging
import urllib.parse
from peewee import SqliteDatabase, PostgresqlDatabase, Model, CharField, FloatField, IntegerField, DateTimeField, TextField, OperationalError, InterfaceError
from playhouse.pool import PooledPostgresqlDatabase
import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
        db = PooledPostgresqlDatabase(
            db_name,
            max_connections=10,
            stale_timeout=300,  # Recycle connections idle for > 5 min
            user=db_user,
            password=db_password,
            host=db_host,
            port=db_port,
            sslmode='require' # Neon/Supabase require SSL
        )
        logger.info("Database: Initialized Pooled PostgreSQL database connection from DATABASE_URL.")
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

def retry_on_db_error(default_value=None):
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (OperationalError, InterfaceError) as e:
                logger.warning(f"Database connection error in {func.__name__}: {e}. Reconnecting and retrying...")
                try:
                    if not db.is_closed():
                        db.close()
                    db.connect(reuse_if_open=True)
                except Exception as reconnect_err:
                    logger.error(f"Failed to reconnect to database: {reconnect_err}")
                
                # Retry once
                try:
                    return func(*args, **kwargs)
                except Exception as retry_err:
                    logger.error(f"DB Error in {func.__name__} after retry: {retry_err}")
                    return default_value
        return wrapper
    return decorator

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
        
    db_type = "PostgreSQL" if isinstance(db, PostgresqlDatabase) else "SQLite"
    logger.info(f"Database: {db_type} database (name: {db.database}) initialized and tables verified.")

@retry_on_db_error(default_value=None)
def save_crypto_trade(symbol: str, amount: float, entry_price: float, tp: float, sl: float, direction: str = 'BUY'):
    try:
        with db.connection_context():
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
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error saving crypto trade: {e}")

@retry_on_db_error(default_value=None)
def remove_crypto_trade(symbol: str):
    try:
        with db.connection_context():
            query = CryptoTradeRecord.delete().where(CryptoTradeRecord.symbol == symbol)
            query.execute()
            logger.info(f"DB: Removed active trade for {symbol}")
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error removing crypto trade: {e}")

@retry_on_db_error(default_value={})
def load_all_crypto_trades() -> dict:
    try:
        trades = {}
        with db.connection_context():
            for r in CryptoTradeRecord.select():
                trades[r.symbol] = {
                    'amount': r.amount,
                    'entry_price': r.entry_price,
                    'take_profit': r.take_profit,
                    'stop_loss': r.stop_loss,
                    'direction': getattr(r, 'direction', 'BUY')
                }
        return trades
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error loading crypto trades: {e}")
        return {}

@retry_on_db_error(default_value=None)
def save_stock_spread(symbol: str, spread_info: dict, contracts: int, margin_lockup: float = None):
    try:
        width = spread_info['spread_width']
        if margin_lockup is None:
            margin_lockup = spread_info.get('margin_lockup', width * 100.0 * contracts)
            
        spread_copy = spread_info.copy()
        spread_copy['contracts'] = contracts
        spread_copy['margin_lockup'] = margin_lockup
        
        with db.connection_context():
            # Delete if exists to avoid unique constraint issue
            StockSpreadRecord.delete().where(StockSpreadRecord.symbol == symbol).execute()
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
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error saving stock spread: {e}")

@retry_on_db_error(default_value=None)
def remove_stock_spread(symbol: str):
    try:
        with db.connection_context():
            query = StockSpreadRecord.delete().where(StockSpreadRecord.symbol == symbol)
            query.execute()
            logger.info(f"DB: Removed active options spread for {symbol}")
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error removing stock spread: {e}")

@retry_on_db_error(default_value={})
def load_all_stock_spreads() -> dict:
    try:
        spreads = {}
        with db.connection_context():
            for r in StockSpreadRecord.select():
                spread_info = json.loads(r.raw_spread_info)
                spread_info['contracts'] = r.contracts
                spread_info['margin_lockup'] = r.margin_lockup
                spreads[r.symbol] = spread_info
        return spreads
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error loading stock spreads: {e}")
        return {}

@retry_on_db_error(default_value=None)
def save_closed_trade(asset_type: str, symbol: str, direction: str, amount: float, entry_price: float, exit_price: float, pnl: float, reason: str):
    try:
        with db.connection_context():
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
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error saving closed trade history: {e}")

@retry_on_db_error(default_value=[])
def load_closed_trades_history(limit: int = 20) -> list:
    try:
        records = []
        with db.connection_context():
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
    except (OperationalError, InterfaceError):
        raise
    except Exception as e:
        logger.error(f"DB Error loading closed trades history: {e}")
        return []
