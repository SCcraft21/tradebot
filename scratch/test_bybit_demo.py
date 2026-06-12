import ccxt
import yaml

def test_bybit():
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    api_key = config['credentials']['api_key']
    api_secret = config['credentials']['api_secret']
    
    print(f"Testing API Key: {api_key}")
    
    # Test 1: Demo Trading Option
    print("\n--- Testing Demo Trading ---")
    exchange = ccxt.bybit({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
    })
    exchange.enableDemoTrading(True)
    try:
        balance = exchange.fetch_balance()
        print("Success! Fetched balance on Demo Trading.")
        print(balance['total'])
    except Exception as e:
        print(f"Demo Trading failed: {e}")

if __name__ == '__main__':
    test_bybit()
