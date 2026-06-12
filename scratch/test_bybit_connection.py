import ccxt
import yaml

def test_connection():
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    api_key = config['credentials']['api_key']
    api_secret = config['credentials']['api_secret']
    
    print(f"Testing API Key: {api_key}")
    
    # Test 1: Mainnet (Live)
    print("\n--- Testing Mainnet ---")
    exchange_live = ccxt.bybit({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
    })
    try:
        balance = exchange_live.fetch_balance()
        print("Success! Fetched balance on Mainnet.")
    except Exception as e:
        print(f"Mainnet failed: {e}")
        
    # Test 2: Testnet (Sandbox)
    print("\n--- Testing Testnet ---")
    exchange_testnet = ccxt.bybit({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
    })
    exchange_testnet.set_sandbox_mode(True)
    try:
        balance = exchange_testnet.fetch_balance()
        print("Success! Fetched balance on Testnet.")
    except Exception as e:
        print(f"Testnet failed: {e}")

if __name__ == '__main__':
    test_connection()
