import os
import sqlite3
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger("migration")

def run_migration():
    # 1. Load config and check DATABASE_URL
    import db
    
    if not db.DATABASE_URL or not (db.DATABASE_URL.startswith("postgres://") or db.DATABASE_URL.startswith("postgresql://")):
        logger.error("DATABASE_URL is not set or does not point to a PostgreSQL database in .env!")
        logger.info("Please set DATABASE_URL in your .env file first.")
        return False
        
    logger.info("Initializing PostgreSQL schema and tables...")
    try:
        db.init_db()
    except Exception as e:
        logger.error(f"Failed to initialize PostgreSQL tables: {e}")
        return False

    sqlite_db_path = 'trading_state.db'
    if not os.path.exists(sqlite_db_path):
        logger.warning(f"Local SQLite database '{sqlite_db_path}' not found. Nothing to migrate.")
        return True

    logger.info(f"Connecting to local SQLite database '{sqlite_db_path}'...")
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    sqlite_cur = sqlite_conn.cursor()

    # Migrate Crypto Trade Records
    try:
        sqlite_cur.execute("SELECT symbol, amount, entry_price, take_profit, stop_loss, direction, created_at FROM cryptotraderecord")
        crypto_rows = sqlite_cur.fetchall()
        logger.info(f"Found {len(crypto_rows)} crypto trade records in SQLite.")
        
        migrated_crypto = 0
        for row in crypto_rows:
            symbol, amount, entry_price, take_profit, stop_loss, direction, created_at_str = row
            
            # Parse datetime
            try:
                created_at = datetime.fromisoformat(created_at_str) if created_at_str else datetime.now()
            except ValueError:
                created_at = datetime.now()

            # Using get_or_create to prevent duplicate key constraint violations
            record, created = db.CryptoTradeRecord.get_or_create(
                symbol=symbol,
                defaults={
                    'amount': amount,
                    'entry_price': entry_price,
                    'take_profit': take_profit,
                    'stop_loss': stop_loss,
                    'direction': direction or 'BUY',
                    'created_at': created_at
                }
            )
            if created:
                migrated_crypto += 1
            else:
                logger.info(f"Crypto trade for {symbol} already exists in Postgres. Skipping.")
                
        logger.info(f"Successfully migrated {migrated_crypto} crypto trade records to Postgres.")
    except Exception as e:
        logger.error(f"Error migrating crypto trades: {e}")

    # Migrate Stock Option Spread Records
    try:
        sqlite_cur.execute("SELECT symbol, strategy_type, expiration, dte, underlying_price, short_strike, long_strike, net_credit, spread_width, contracts, margin_lockup, raw_spread_info, created_at FROM stockspreadrecord")
        spread_rows = sqlite_cur.fetchall()
        logger.info(f"Found {len(spread_rows)} stock spread records in SQLite.")
        
        migrated_spreads = 0
        for row in spread_rows:
            symbol, strategy_type, expiration, dte, underlying_price, short_strike, long_strike, net_credit, spread_width, contracts, margin_lockup, raw_spread_info, created_at_str = row
            
            try:
                created_at = datetime.fromisoformat(created_at_str) if created_at_str else datetime.now()
            except ValueError:
                created_at = datetime.now()

            record, created = db.StockSpreadRecord.get_or_create(
                symbol=symbol,
                defaults={
                    'strategy_type': strategy_type,
                    'expiration': expiration,
                    'dte': dte,
                    'underlying_price': underlying_price,
                    'short_strike': short_strike or 0.0,
                    'long_strike': long_strike or 0.0,
                    'net_credit': net_credit,
                    'spread_width': spread_width,
                    'contracts': contracts,
                    'margin_lockup': margin_lockup,
                    'raw_spread_info': raw_spread_info,
                    'created_at': created_at
                }
            )
            if created:
                migrated_spreads += 1
            else:
                logger.info(f"Stock spread for {symbol} already exists in Postgres. Skipping.")
                
        logger.info(f"Successfully migrated {migrated_spreads} stock spread records to Postgres.")
    except Exception as e:
        logger.error(f"Error migrating stock spreads: {e}")

    # Migrate Trade History Records
    try:
        sqlite_cur.execute("SELECT asset_type, symbol, direction, amount, entry_price, exit_price, pnl, reason, closed_at FROM tradehistoryrecord")
        history_rows = sqlite_cur.fetchall()
        logger.info(f"Found {len(history_rows)} historical trade records in SQLite.")
        
        migrated_history = 0
        for row in history_rows:
            asset_type, symbol, direction, amount, entry_price, exit_price, pnl, reason, closed_at_str = row
            
            try:
                closed_at = datetime.fromisoformat(closed_at_str) if closed_at_str else datetime.now()
            except ValueError:
                closed_at = datetime.now()

            record, created = db.TradeHistoryRecord.get_or_create(
                asset_type=asset_type,
                symbol=symbol,
                direction=direction,
                amount=amount,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                reason=reason,
                closed_at=closed_at
            )
            if created:
                migrated_history += 1
                
        logger.info(f"Successfully migrated {migrated_history} historical trade records to Postgres.")
    except Exception as e:
        logger.error(f"Error migrating historical trades: {e}")

    sqlite_conn.close()
    logger.info("Migration complete!")
    return True

if __name__ == '__main__':
    run_migration()
